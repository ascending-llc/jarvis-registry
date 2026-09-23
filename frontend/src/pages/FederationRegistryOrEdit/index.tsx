import { ArrowPathIcon, CalendarIcon, ClockIcon, TrashIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { FiServer } from 'react-icons/fi';
import { HiOutlineShare } from 'react-icons/hi2';
import { useNavigate, useSearchParams } from 'react-router-dom';

import ShareModal from '@/components/ShareModal';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import { useExternalProviderSync } from '@/hooks/useExternalProviderSync';
import SERVICES from '@/services';
import {
  confirmGithubAuthorizationRedirect,
  redirectToGithubAuthorization,
} from '@/services/externalProvider/githubAuthorization';
import {
  clearGithubOauthIntent,
  consumeGithubOauthIntent,
  type GithubOauthIntent,
} from '@/services/externalProvider/oauthIntent';
import { getSkillSyncSourceCallbackUrl } from '@/services/externalProvider/sync';
import type { Federation } from '@/services/federation/type';
import type { SkillSyncSourceDetail } from '@/services/skillSyncSource/type';
import UTILS from '@/utils';
import { getErrorMessage } from '@/utils/getErrorMessage';

import {
  buildGithubCreatePayload,
  buildGithubUpdatePayload,
  DEFAULT_GITHUB_REF,
  hasGithubFormChanges,
  normalizePaths,
  normalizeTags,
  validateGithubForm,
} from './formUtils';
import GithubAuthorizationPanel from './GithubAuthorizationPanel';
import GithubLastSyncDetails from './GithubLastSyncDetails';
import MainConfigForm from './MainConfigForm';
import { getLatestFinishedSyncJob } from './skillSyncJobUtils';
import type { FederationFormConfig } from './types';

const INIT_DATA: FederationFormConfig = {
  providerType: 'aws_agentcore',
  displayName: '',
  description: '',
  region: '',
  assumeRoleArn: '',
  resourceTagsFilter: '',
  projectEndpoint: '',
  tenantId: '',
  clientId: '',
  clientSecret: '',
  tags: [],
  owner: '',
  repo: '',
  ref: DEFAULT_GITHUB_REF,
  paths: ['skills/'],
  githubAppClientId: '',
  githubAppClientSecret: '',
};

const getGithubFormData = (data: SkillSyncSourceDetail): FederationFormConfig => ({
  ...INIT_DATA,
  providerType: 'github',
  displayName: data.displayName,
  description: data.description || '',
  tags: [...data.tags],
  owner: data.owner,
  repo: data.repo,
  ref: data.ref,
  paths: [...data.paths],
  githubAppClientId: data.githubAppClientId,
  githubAppClientSecret: '',
});

const FederationRegistryOrEdit: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const id = searchParams.get('id');
  const isGithubSource = searchParams.get('provider') === 'github';
  const { showToast } = useGlobal();
  const { refreshFederationData, handleFederationUpdate } = useServer();

  const [loading, setLoading] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [federation, setFederation] = useState<Federation | null>(null);
  const [skillSyncSource, setSkillSyncSource] = useState<SkillSyncSourceDetail | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const currentProviderIdRef = useRef(id);
  const detailRequestGenerationRef = useRef(0);
  const oauthCallbackHandledRef = useRef(false);
  currentProviderIdRef.current = id;

  const [formData, setFormData] = useState<FederationFormConfig>(INIT_DATA);
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const [testConnectionLoading, setTestConnectionLoading] = useState(false);
  const [testConnectionResult, setTestConnectionResult] = useState<{
    success: boolean;
    message: string;
  } | null>(null);

  const isEditMode = Boolean(id);
  const isReadOnly = searchParams.get('isReadOnly') === 'true';
  const activeProvider = isGithubSource ? skillSyncSource : federation;
  const isGithubForm = formData.providerType === 'github';
  const githubCallbackUrl = getSkillSyncSourceCallbackUrl();
  const hasUnsavedGithubChanges =
    isGithubSource && skillSyncSource !== null && hasGithubFormChanges(skillSyncSource, formData);

  const goBack = () => navigate(-1);

  const getDetail = useCallback(async () => {
    const providerId = id;
    if (!providerId) return;

    const detailRequestGeneration = ++detailRequestGenerationRef.current;
    setLoadingDetail(true);
    try {
      if (isGithubSource) {
        const data = await SERVICES.SKILL_SYNC_SOURCE.getSkillSyncSource(providerId);
        if (
          detailRequestGeneration !== detailRequestGenerationRef.current ||
          currentProviderIdRef.current !== providerId
        ) {
          return;
        }
        setFederation(null);
        setSkillSyncSource(data);
        setFormData(getGithubFormData(data));
        return;
      }

      const data = await SERVICES.FEDERATION.getFederation(providerId);
      if (
        detailRequestGeneration !== detailRequestGenerationRef.current ||
        currentProviderIdRef.current !== providerId
      ) {
        return;
      }
      setSkillSyncSource(null);
      setFederation(data);
      setFormData({
        ...INIT_DATA,
        providerType: data.providerType,
        displayName: data.displayName,
        description: data.description || '',
        region: data.providerConfig?.region || '',
        assumeRoleArn: data.providerConfig?.assumeRoleArn || '',
        resourceTagsFilter: data.providerConfig?.resourceTagsFilter
          ? Object.entries(data.providerConfig.resourceTagsFilter)
              .map(([key, value]) => `${key}:${value}`)
              .join(', ')
          : '',
        projectEndpoint: data.providerConfig?.projectEndpoint || '',
        tenantId: data.providerConfig?.tenantId || '',
        clientId: data.providerConfig?.clientId || '',
        clientSecret: '',
      });
    } catch (error: unknown) {
      if (detailRequestGeneration === detailRequestGenerationRef.current) {
        showToast(getErrorMessage(error, 'Failed to fetch external provider details'), 'error');
      }
    } finally {
      if (detailRequestGeneration === detailRequestGenerationRef.current) setLoadingDetail(false);
    }
  }, [id, isGithubSource, showToast]);

  const refreshDetail = useCallback(() => {
    void getDetail();
  }, [getDetail]);

  const sourceActiveJob = skillSyncSource?.recentJobs.find(job => job.status === 'pending' || job.status === 'syncing');
  const activeJobId = isGithubSource
    ? sourceActiveJob?.id || skillSyncSource?.lastSync?.jobId
    : federation?.lastSync?.jobId;
  const canEditProvider = activeProvider?.permissions.EDIT ?? false;
  const lastFinishedGithubJob = skillSyncSource ? getLatestFinishedSyncJob(skillSyncSource.recentJobs) : null;

  const { syncView, startSync, runSyncAction, stopPolling } = useExternalProviderSync({
    providerId: id,
    isGithub: isGithubSource,
    canEdit: canEditProvider,
    serverStatus: activeProvider?.syncStatus,
    syncMessage: activeProvider?.syncMessage,
    serverJobId: activeJobId,
    onSettled: refreshDetail,
  });

  useEffect(() => {
    stopPolling();
    detailRequestGenerationRef.current += 1;
    setFederation(null);
    setSkillSyncSource(null);
    setErrors({});
    setTestConnectionResult(null);
    oauthCallbackHandledRef.current = false;

    if (id) {
      void getDetail();
      return;
    }
    setFormData(INIT_DATA);
    setLoadingDetail(false);
  }, [getDetail, id, stopPolling]);

  const validate = (data: FederationFormConfig): boolean => {
    if (data.providerType === 'github') {
      const githubErrors = validateGithubForm(data, {
        requireSecret: !(isEditMode && skillSyncSource?.hasClientSecret),
      });
      setErrors(githubErrors);
      return Object.keys(githubErrors).length === 0;
    }

    const newErrors: Record<string, string> = {};
    const displayName = data.displayName.trim();
    if (!displayName) newErrors.displayName = 'Display Name is required';
    else if (displayName.length > 128) newErrors.displayName = 'Display Name must be 128 characters or fewer';

    if (data.providerType === 'aws_agentcore') {
      if (!data.region.trim()) newErrors.region = 'AWS Region is required';
      if (!data.assumeRoleArn.trim()) newErrors.assumeRoleArn = 'Role ARN is required';
    } else {
      if (!data.projectEndpoint.trim()) newErrors.projectEndpoint = 'Project Endpoint is required';
      const servicePrincipalFields: Array<{ key: 'tenantId' | 'clientId' | 'clientSecret'; label: string }> = [
        { key: 'tenantId', label: 'Tenant ID' },
        { key: 'clientId', label: 'Client ID' },
        { key: 'clientSecret', label: 'Client Secret' },
      ];
      const filledCount = servicePrincipalFields.filter(({ key }) => data[key].trim()).length;
      if (filledCount > 0 && filledCount < servicePrincipalFields.length) {
        for (const { key, label } of servicePrincipalFields) {
          if (!data[key].trim()) {
            newErrors[key] = `${label} is required when configuring service-principal authentication`;
          }
        }
      }
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const updateField = <Field extends keyof FederationFormConfig>(field: Field, value: FederationFormConfig[Field]) => {
    setFormData(current => ({ ...current, [field]: value }));
    setErrors(current => (current[field] ? { ...current, [field]: undefined } : current));
    if (field === 'providerType' || isGithubForm) setTestConnectionResult(null);
  };

  const parseTagsFilter = (input: string): Record<string, string> | undefined => {
    const filter: Record<string, string> = {};
    for (const pair of input.trim().split(',')) {
      const [key, value] = pair.split(':').map(item => item.trim());
      if (key && value) filter[key] = value;
    }
    return Object.keys(filter).length > 0 ? filter : undefined;
  };

  const handleDelete = async () => {
    if (!id || !window.confirm('Are you sure you want to delete this external provider?')) return;
    setLoading(true);
    try {
      if (isGithubSource) {
        await SERVICES.SKILL_SYNC_SOURCE.deleteSkillSyncSource(id);
        showToast('External Provider deletion started', 'success');
      } else {
        await SERVICES.FEDERATION.deleteFederation(id);
        showToast('External Provider deleted successfully', 'success');
      }
      await refreshFederationData(true);
      navigate('/?tab=external', { replace: true });
    } catch (error: unknown) {
      showToast(getErrorMessage(error, 'Failed to delete external provider'), 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleCopyGithubCallbackUrl = async () => {
    try {
      await navigator.clipboard.writeText(githubCallbackUrl);
      showToast('Callback URL copied', 'success');
    } catch {
      showToast('Failed to copy callback URL', 'error');
    }
  };

  const handleConnectGithub = () => {
    if (!id) return;
    // After the callback the page validates the new authorization with a test-connect.
    redirectToGithubAuthorization(id, 'test');
  };

  const runGithubConnectionTest = useCallback(
    async ({
      redirectIntent = 'test',
      allowAuthorizationRedirect = true,
    }: {
      redirectIntent?: GithubOauthIntent;
      allowAuthorizationRedirect?: boolean;
    } = {}): Promise<boolean> => {
      if (!id) return false;

      setTestConnectionLoading(true);
      setTestConnectionResult(null);
      try {
        const result = await SERVICES.SKILL_SYNC_SOURCE.syncSkillSyncSource(id, { dryRun: true });
        if (result.needsAuthorization) {
          const redirected = allowAuthorizationRedirect && confirmGithubAuthorizationRedirect(id, redirectIntent);
          if (!redirected) {
            setTestConnectionResult({
              success: false,
              message: allowAuthorizationRedirect
                ? 'GitHub authorization is required. Use Connect GitHub to authorize.'
                : 'GitHub authorization did not provide a usable token. Please connect again.',
            });
          }
          return false;
        }

        const success = result.ok;
        setTestConnectionResult({
          success,
          message: success ? 'Connected successfully' : result.detail || 'Connection validation failed',
        });
        return success;
      } catch (error: unknown) {
        setTestConnectionResult({
          success: false,
          message: getErrorMessage(error, 'Connection failed — check your settings and try again'),
        });
        return false;
      } finally {
        setTestConnectionLoading(false);
      }
    },
    [id],
  );

  const handleTestConnection = async () => {
    if (!id || !validate(formData)) return;

    if (isGithubForm) {
      if (skillSyncSource === null || hasUnsavedGithubChanges) return;
      await runGithubConnectionTest();
      return;
    }

    setTestConnectionLoading(true);
    setTestConnectionResult(null);
    try {
      const providerConfig =
        formData.providerType === 'aws_agentcore'
          ? {
              region: formData.region,
              assumeRoleArn: formData.assumeRoleArn,
              resourceTagsFilter: parseTagsFilter(formData.resourceTagsFilter),
            }
          : {
              projectEndpoint: formData.projectEndpoint,
              tenantId: formData.tenantId,
              clientId: formData.clientId,
              clientSecret: formData.clientSecret,
            };
      const result = await SERVICES.FEDERATION.syncFederation(
        id,
        { dryRun: true, providerConfig },
        { timeout: 120000 },
      );
      const summary = 'summary' in result ? result.summary : null;
      const discoveredMcp = summary?.discoveredMcpServers ?? 0;
      const discoveredAgents = summary?.discoveredAgents ?? 0;
      setTestConnectionResult({
        success: true,
        message: `Connected — discovered ${discoveredMcp} MCP server${discoveredMcp === 1 ? '' : 's'}, ${discoveredAgents} agent${discoveredAgents === 1 ? '' : 's'}`,
      });
    } catch (error: unknown) {
      setTestConnectionResult({
        success: false,
        message: getErrorMessage(error, 'Connection failed — check your settings and try again'),
      });
    } finally {
      setTestConnectionLoading(false);
    }
  };

  useEffect(() => {
    if (!isGithubSource || !id || oauthCallbackHandledRef.current) return;

    const oauthError = searchParams.get('error');
    const oauthStatus = searchParams.get('status');
    if (!oauthError && !oauthStatus) return;

    oauthCallbackHandledRef.current = true;
    const nextSearchParams = new URLSearchParams(searchParams);
    nextSearchParams.delete('error');
    nextSearchParams.delete('status');
    setSearchParams(nextSearchParams, { replace: true });

    if (oauthError === 'auth_failed') {
      clearGithubOauthIntent(id);
      showToast('GitHub authorization failed. Please try connecting again.', 'error');
      return;
    }

    if (oauthStatus !== 'connected') return;

    const intent = consumeGithubOauthIntent(id);
    showToast('GitHub connected. Validating the connection now.', 'info');
    void (async () => {
      const connected = await runGithubConnectionTest({ allowAuthorizationRedirect: false });
      if (intent === 'sync' && connected) await startSync({ allowAuthorizationRedirect: false });
      // Pick up the new per-user authorization state (and any job the sync just queued).
      await getDetail();
    })();
  }, [getDetail, id, isGithubSource, runGithubConnectionTest, searchParams, setSearchParams, showToast, startSync]);

  const handleSave = async () => {
    const preparedForm = isGithubForm
      ? {
          ...formData,
          tags: normalizeTags(formData.tags),
          paths: normalizePaths(formData.paths),
        }
      : formData;
    setFormData(preparedForm);
    if (!validate(preparedForm)) return;

    setLoading(true);
    try {
      if (preparedForm.providerType === 'github') {
        if (isEditMode && id && skillSyncSource) {
          const payload = buildGithubUpdatePayload(skillSyncSource, preparedForm);
          if (Object.keys(payload).length === 0) {
            showToast('No changes to save', 'info');
            goBack();
            return;
          }
          const result = await SERVICES.SKILL_SYNC_SOURCE.updateSkillSyncSource(id, payload);
          const updated = 'providerType' in result ? result : await SERVICES.SKILL_SYNC_SOURCE.getSkillSyncSource(id);
          setSkillSyncSource(updated);
          setFormData(getGithubFormData(updated));
          showToast('External Provider updated successfully', 'success');
        } else {
          await SERVICES.SKILL_SYNC_SOURCE.createSkillSyncSource(buildGithubCreatePayload(preparedForm));
          showToast('External Provider added successfully', 'success');
        }
        await refreshFederationData(true);
        goBack();
        return;
      }

      const providerConfig =
        preparedForm.providerType === 'aws_agentcore'
          ? {
              region: preparedForm.region,
              assumeRoleArn: preparedForm.assumeRoleArn,
              resourceTagsFilter: parseTagsFilter(preparedForm.resourceTagsFilter),
            }
          : {
              projectEndpoint: preparedForm.projectEndpoint,
              tenantId: preparedForm.tenantId,
              clientId: preparedForm.clientId,
              clientSecret: preparedForm.clientSecret,
            };

      if (isEditMode && id && federation) {
        const result = await SERVICES.FEDERATION.updateFederation(id, {
          displayName: preparedForm.displayName,
          description: preparedForm.description || undefined,
          providerConfig,
          version: federation.version,
          syncAfterUpdate: true,
        });
        showToast('External Provider updated successfully', 'success');
        handleFederationUpdate(id, { displayName: result.displayName, description: result.description });
      } else {
        await SERVICES.FEDERATION.createFederation({
          providerType: preparedForm.providerType,
          displayName: preparedForm.displayName,
          description: preparedForm.description || undefined,
          providerConfig,
        });
        showToast('External Provider added successfully', 'success');
        await refreshFederationData(true);
      }
      goBack();
    } catch (error: unknown) {
      showToast(getErrorMessage(error, 'Failed to save external provider'), 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {shareOpen && id && (
        <ShareModal
          itemName={formData.displayName || activeProvider?.displayName || 'External Provider'}
          resourceId={id}
          resourceType={isGithubSource ? 'skill_sync_source' : 'federation'}
          isOpen={shareOpen}
          onClose={() => setShareOpen(false)}
        />
      )}
      <div className='custom-scrollbar -mr-4 h-full overflow-y-auto sm:-mr-6 lg:-mr-8'>
        <div className='mx-auto flex min-h-full w-3/4 flex-col rounded-lg bg-[var(--jarvis-card)]'>
          <div className='flex items-center gap-4 border-b border-[color:var(--jarvis-border)] px-6 py-6'>
            <div className='flex items-center justify-center rounded-xl bg-[var(--jarvis-primary-soft)] p-3'>
              <FiServer className='h-8 w-8 text-[var(--jarvis-primary)]' />
            </div>
            <div>
              <h1 className='text-2xl font-bold text-[var(--jarvis-text-strong)]'>
                {isReadOnly ? 'View External' : isEditMode ? 'Edit External' : 'Register External'}
              </h1>
              <p className='mt-0.5 text-base text-[var(--jarvis-muted)]'>
                Configure remote discovery for MCP servers, agents, and skills
              </p>
            </div>
          </div>

          <div className='flex flex-1 flex-col px-6 py-4'>
            {loadingDetail ? (
              <div className='flex min-h-[200px] flex-1 items-center justify-center'>
                <div className='h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-primary)]' />
              </div>
            ) : (
              <>
                {isEditMode && activeProvider && (
                  <div className='mb-4 flex flex-wrap gap-4 text-sm text-[var(--jarvis-muted)]'>
                    <span className='flex items-center gap-1.5'>
                      <CalendarIcon className='h-3.5 w-3.5' />
                      Created:{' '}
                      {new Date(activeProvider.createdAt).toLocaleDateString(undefined, {
                        month: 'short',
                        day: 'numeric',
                        year: 'numeric',
                      })}
                    </span>
                    <span className='flex items-center gap-1.5'>
                      <ClockIcon className='h-3.5 w-3.5' />
                      Last synced: {UTILS.formatTimeSince(activeProvider.lastSync?.finishedAt) ?? 'Never'}
                    </span>
                  </div>
                )}
                {isGithubSource && skillSyncSource && (
                  <GithubAuthorizationPanel
                    connected={skillSyncSource.authorization.connected}
                    canConnect={skillSyncSource.permissions.EDIT}
                    connectDisabledReason={
                      !isReadOnly && hasUnsavedGithubChanges ? 'Save changes before connecting' : undefined
                    }
                    onConnect={handleConnectGithub}
                  />
                )}
                <MainConfigForm
                  formData={formData}
                  updateField={updateField}
                  errors={errors}
                  isEditMode={isEditMode}
                  isReadOnly={isReadOnly}
                  hasGithubClientSecret={skillSyncSource?.hasClientSecret}
                  githubCallbackUrl={githubCallbackUrl}
                  onCopyGithubCallbackUrl={() => void handleCopyGithubCallbackUrl()}
                  onTestConnection={() => void handleTestConnection()}
                  testConnectionLoading={testConnectionLoading}
                  testConnectionDisabled={
                    isGithubSource && (loadingDetail || skillSyncSource === null || hasUnsavedGithubChanges)
                  }
                  testConnectionDisabledReason={
                    isGithubSource && hasUnsavedGithubChanges
                      ? 'Save changes before testing'
                      : isGithubSource && skillSyncSource === null
                        ? 'Provider details must load before testing'
                        : undefined
                  }
                  testConnectionResult={testConnectionResult}
                />
              </>
            )}

            {isReadOnly && activeProvider && (
              <div className='mt-8 border-t border-[color:var(--jarvis-border)] pt-6'>
                <h3 className='mb-4 text-lg font-medium text-[var(--jarvis-text-strong)]'>Discovered Resources</h3>
                {isGithubSource && skillSyncSource ? (
                  <div className='grid grid-cols-2 gap-4'>
                    <div className='rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] p-5 text-center'>
                      <div className='text-3xl font-bold text-[var(--jarvis-primary)]'>
                        {skillSyncSource.stats.skillCount}
                      </div>
                      <div className='mt-1 text-sm text-[var(--jarvis-muted)]'>Skills</div>
                    </div>
                    <div className='rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] p-5 text-center'>
                      <div className='text-3xl font-bold text-[var(--jarvis-info-text)]'>
                        {skillSyncSource.stats.fileCount}
                      </div>
                      <div className='mt-1 text-sm text-[var(--jarvis-muted)]'>Files</div>
                    </div>
                  </div>
                ) : federation ? (
                  <div className='grid grid-cols-4 gap-4'>
                    {[
                      ['MCP Servers', federation.stats?.mcpServerCount ?? 0, 'text-[var(--jarvis-primary)]'],
                      ['AI Agents', federation.stats?.agentCount ?? 0, 'text-[var(--jarvis-success-text)]'],
                      ['Total Imported', federation.stats?.importedTotal ?? 0, 'text-[var(--jarvis-info-text)]'],
                      ['Total Unimported', federation.stats?.unimportedTotal ?? 0, 'text-[var(--jarvis-danger-text)]'],
                    ].map(([label, value, color]) => (
                      <div
                        key={String(label)}
                        className='rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] p-5 text-center'
                      >
                        <div className={`text-3xl font-bold ${color}`}>{value}</div>
                        <div className='mt-1 text-sm text-[var(--jarvis-muted)]'>{label}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            )}

            {isReadOnly && isGithubSource && lastFinishedGithubJob && (
              <GithubLastSyncDetails job={lastFinishedGithubJob} />
            )}
          </div>

          <div className='flex flex-wrap items-center justify-between gap-4 border-t border-[color:var(--jarvis-border)] px-6 py-4'>
            <div className='flex items-center gap-3'>
              {isEditMode && !isReadOnly && activeProvider?.permissions.DELETE && (
                <button
                  type='button'
                  onClick={() => void handleDelete()}
                  disabled={loading}
                  className='inline-flex items-center rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-4 py-2 text-sm font-medium text-[var(--jarvis-danger-text)] shadow-sm hover:bg-[var(--jarvis-danger-soft)] disabled:cursor-not-allowed disabled:opacity-50'
                >
                  <TrashIcon className='h-4 w-4' />
                </button>
              )}
              {isEditMode && id && activeProvider?.permissions.SHARE && (
                <button
                  type='button'
                  onClick={() => setShareOpen(true)}
                  disabled={loading || loadingDetail}
                  className='inline-flex items-center rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-4 py-2 text-sm font-medium text-[var(--jarvis-primary)] shadow-sm hover:bg-[var(--jarvis-primary-soft)] disabled:cursor-not-allowed disabled:opacity-50'
                >
                  <HiOutlineShare className='h-4 w-4' />
                </button>
              )}
            </div>

            <div className='flex gap-3'>
              <button
                type='button'
                onClick={goBack}
                disabled={loading}
                className='min-w-[80px] rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-4 py-2 text-sm font-medium text-[var(--jarvis-text)] shadow-sm hover:bg-[var(--jarvis-card-muted)] disabled:cursor-not-allowed disabled:opacity-50 sm:min-w-[120px] md:min-w-[160px]'
              >
                {isReadOnly ? 'Back' : 'Cancel'}
              </button>

              {isReadOnly && activeProvider && (
                <>
                  {syncView.kind !== 'idle' && (
                    <span
                      className='self-center text-sm text-[var(--jarvis-muted)]'
                      aria-live='polite'
                      title={syncView.detail ?? undefined}
                    >
                      {syncView.label}
                    </span>
                  )}
                  {canEditProvider && (
                    <button
                      type='button'
                      onClick={runSyncAction}
                      disabled={loading || loadingDetail || syncView.action === 'none'}
                      className='inline-flex min-w-[80px] items-center justify-center gap-2 rounded-md border border-[var(--jarvis-primary-soft)] bg-[var(--jarvis-card)] px-4 py-2 text-sm font-medium text-[var(--jarvis-primary)] shadow-sm hover:bg-[var(--jarvis-primary-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 sm:min-w-[120px] md:min-w-[160px]'
                    >
                      <ArrowPathIcon className={`h-4 w-4 ${syncView.isBusy ? 'animate-spin' : ''}`} />
                      {syncView.actionLabel}
                    </button>
                  )}
                </>
              )}

              {!isReadOnly && (
                <button
                  type='button'
                  onClick={() => void handleSave()}
                  disabled={loading}
                  className='inline-flex min-w-[80px] items-center justify-center gap-2 rounded-md border border-transparent bg-[var(--jarvis-primary-hover)] px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-[var(--jarvis-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50 sm:min-w-[120px] md:min-w-[160px]'
                >
                  {loading && <div className='h-4 w-4 animate-spin rounded-full border-b-2 border-white' />}
                  {isEditMode ? 'Update' : 'Register External'}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

export default FederationRegistryOrEdit;
