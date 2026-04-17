import { ArrowPathIcon, CalendarIcon, ClockIcon, TrashIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useState } from 'react';
import { FiServer } from 'react-icons/fi';
import { HiOutlineShare } from 'react-icons/hi2';
import { useNavigate, useSearchParams } from 'react-router-dom';

import ShareModal from '@/components/ShareModal';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import type { Federation } from '@/services/federation/type';

import MainConfigForm from './MainConfigForm';
import type { FederationFormConfig } from './types';

const formatDistanceToNow = (dateStr: string): string => {
  if (!dateStr) return '';
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days > 0) return `${days} day${days !== 1 ? 's' : ''} ago`;
  if (hours > 0) return `${hours} hour${hours !== 1 ? 's' : ''} ago`;
  if (minutes > 0) return `${minutes} min ago`;
  return 'just now';
};

const INIT_DATA: FederationFormConfig = {
  providerType: 'aws_agentcore',
  displayName: '',
  description: '',
  region: '',
  assumeRoleArn: '',
  resourceTagsFilter: '',
  azureTenantId: '',
  azureSubscriptionId: '',
  azureResourceGroup: '',
};

const FederationRegistryOrEdit: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const id = searchParams.get('id');
  const { showToast } = useGlobal();
  const { refreshFederationData, handleFederationUpdate } = useServer();

  const [loading, setLoading] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [federation, setFederation] = useState<Federation | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);

  const [formData, setFormData] = useState<FederationFormConfig>(INIT_DATA);
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});

  const [testConnectionLoading, setTestConnectionLoading] = useState(false);
  const [testConnectionResult, setTestConnectionResult] = useState<{
    success: boolean;
    message: string;
  } | null>(null);

  const isEditMode = !!id;
  const isReadOnly = searchParams.get('isReadOnly') === 'true';

  useEffect(() => {
    if (id) getDetail();
  }, [id]);

  const goBack = () => {
    navigate(-1);
  };

  const getDetail = async () => {
    if (!id) return;
    setLoadingDetail(true);
    try {
      const data = await SERVICES.FEDERATION.getFederation(id);
      setFederation(data);
      setFormData({
        providerType: data.providerType,
        displayName: data.displayName,
        description: data.description || '',
        region: data.providerConfig?.region || '',
        assumeRoleArn: data.providerConfig?.assumeRoleArn || '',
        resourceTagsFilter: data.providerConfig?.resourceTagsFilter
          ? Object.entries(data.providerConfig.resourceTagsFilter)
              .map(([k, v]) => `${k}:${v}`)
              .join(', ')
          : '',
        azureTenantId: data.providerConfig?.tenantId || '',
        azureSubscriptionId: data.providerConfig?.subscriptionId || '',
        azureResourceGroup: data.providerConfig?.resourceGroup || '',
      });
    } catch (_error: any) {
      showToast(_error?.detail?.message || 'Failed to fetch external registry details', 'error');
    } finally {
      setLoadingDetail(false);
    }
  };

  const validate = () => {
    const newErrors: Record<string, string> = {};

    if (!formData.displayName.trim()) {
      newErrors.displayName = 'Display Name is required';
    }

    if (formData.providerType === 'aws_agentcore') {
      if (!formData.region.trim()) newErrors.region = 'AWS Region is required';
      if (!formData.assumeRoleArn.trim()) newErrors.assumeRoleArn = 'Role ARN is required';
    } else if (formData.providerType === 'azure_ai_foundry') {
      if (!formData.region.trim()) newErrors.region = 'Azure Region is required';
      if (!formData.azureTenantId.trim()) newErrors.azureTenantId = 'Tenant ID is required';
      if (!formData.azureSubscriptionId.trim()) newErrors.azureSubscriptionId = 'Subscription ID is required';
      if (!formData.azureResourceGroup.trim()) newErrors.azureResourceGroup = 'Resource Group is required';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const updateField = (field: keyof FederationFormConfig, value: any) => {
    setFormData(prev => ({ ...prev, [field]: value }));
    if (errors[field]) {
      setErrors(prev => ({ ...prev, [field]: undefined }));
    }
  };

  const parseTagsFilter = (input: string) => {
    const trimmed = input.trim();
    if (!trimmed) return undefined;
    const filter: Record<string, string> = {};
    trimmed.split(',').forEach(pair => {
      const [key, val] = pair.split(':').map(s => s.trim());
      if (key && val) filter[key] = val;
    });
    return Object.keys(filter).length > 0 ? filter : undefined;
  };

  const handleDelete = async () => {
    if (!id) return;
    if (!window.confirm('Are you sure you want to delete this external registry?')) return;

    setLoading(true);
    try {
      await SERVICES.FEDERATION.deleteFederation(id);
      showToast('External Registry deleted successfully', 'success');
      refreshFederationData(true);
      navigate('/', { replace: true });
    } catch (error: any) {
      showToast(error?.detail?.message || 'Failed to delete external registry', 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleTestConnection = async () => {
    if (!id) return;

    if (!validate()) {
      showToast('Please fix form errors before testing', 'error');
      return;
    }

    setTestConnectionLoading(true);
    setTestConnectionResult(null);
    try {
      const isAws = formData.providerType === 'aws_agentcore';
      const providerConfig = isAws
        ? {
            region: formData.region,
            assumeRoleArn: formData.assumeRoleArn,
            resourceTagsFilter: parseTagsFilter(formData.resourceTagsFilter),
          }
        : {
            region: formData.region,
            tenantId: formData.azureTenantId,
            subscriptionId: formData.azureSubscriptionId,
            resourceGroup: formData.azureResourceGroup,
            resourceTagsFilter: parseTagsFilter(formData.resourceTagsFilter),
          };

      const result = await SERVICES.FEDERATION.syncFederation(id, {
        dryRun: true,
        providerConfig,
      });

      const discoveredMcp = result?.summary?.discoveredMcpServers ?? 0;
      const discoveredAgents = result?.summary?.discoveredAgents ?? 0;

      setTestConnectionResult({
        success: true,
        message: `Connected — discovered ${discoveredMcp} MCP server${discoveredMcp !== 1 ? 's' : ''}, ${discoveredAgents} agent${discoveredAgents !== 1 ? 's' : ''}`,
      });
    } catch (error: any) {
      setTestConnectionResult({
        success: false,
        message: error?.detail?.message || 'Connection failed — check your settings and try again',
      });
    } finally {
      setTestConnectionLoading(false);
    }
  };

  const handleSync = async () => {
    if (!id) return;
    setIsSyncing(true);
    try {
      await SERVICES.FEDERATION.syncFederation(id);
      showToast('Sync started successfully', 'success');
    } catch (error: any) {
      showToast(error?.detail?.message || 'Failed to start sync', 'error');
    } finally {
      setTimeout(() => {
        setIsSyncing(false);
        getDetail();
      }, 2000);
    }
  };

  const handleSave = async () => {
    if (!validate()) return;
    setLoading(true);
    try {
      const isAws = formData.providerType === 'aws_agentcore';
      const providerConfig = isAws
        ? {
            region: formData.region,
            assumeRoleArn: formData.assumeRoleArn,
            resourceTagsFilter: parseTagsFilter(formData.resourceTagsFilter),
          }
        : {
            region: formData.region,
            tenantId: formData.azureTenantId,
            subscriptionId: formData.azureSubscriptionId,
            resourceGroup: formData.azureResourceGroup,
            resourceTagsFilter: parseTagsFilter(formData.resourceTagsFilter),
          };

      if (isEditMode && id && federation) {
        const result = await SERVICES.FEDERATION.updateFederation(id, {
          displayName: formData.displayName,
          description: formData.description || undefined,
          providerConfig,
          version: federation.version,
          syncAfterUpdate: true,
        });
        showToast('External Registry updated successfully', 'success');
        handleFederationUpdate(id, {
          displayName: result.displayName,
          description: result.description,
        });
      } else {
        await SERVICES.FEDERATION.createFederation({
          providerType: formData.providerType,
          displayName: formData.displayName,
          description: formData.description || undefined,
          providerConfig,
        });
        showToast('External Registry added successfully', 'success');
        refreshFederationData(true);
      }
      goBack();
    } catch (error: any) {
      showToast(error?.detail?.message || 'Failed to save external registry', 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {shareOpen && id && (
        <ShareModal
          itemName={formData.displayName || federation?.displayName || 'External Registry'}
          resourceId={id}
          resourceType='federation'
          isOpen={shareOpen}
          onClose={() => setShareOpen(false)}
        />
      )}
      <div className='h-full overflow-y-auto custom-scrollbar -mr-4 sm:-mr-6 lg:-mr-8'>
        <div className='mx-auto flex flex-col w-3/4 min-h-full bg-white dark:bg-gray-800 rounded-lg'>
        {/* Header */}
        <div className='px-6 py-6 flex items-center gap-4 border-b border-gray-100 dark:border-gray-700'>
          <div className='flex items-center justify-center p-3 rounded-xl bg-[#F3E8FF] dark:bg-purple-900/30'>
            <FiServer className='h-8 w-8 text-purple-600 dark:text-purple-300' />
          </div>
          <div>
            <h1 className='text-2xl font-bold text-gray-900 dark:text-white'>
              {isReadOnly ? 'View External' : isEditMode ? 'Edit External' : 'Register External'}
            </h1>
            <p className='text-base text-gray-500 dark:text-gray-400 mt-0.5'>
              Configure remote discovery for MCP servers and agents
            </p>
          </div>
        </div>

        {/* Content */}
        <div className='px-6 py-4 flex-1 flex flex-col'>
          {loadingDetail ? (
            <div className='flex-1 flex items-center justify-center min-h-[200px]'>
              <div className='animate-spin rounded-full h-8 w-8 border-b-2 border-purple-600'></div>
            </div>
          ) : (
            <>
              {isEditMode && federation && (
                <div className='mb-4 flex flex-wrap gap-4 text-sm text-gray-500 dark:text-gray-400'>
                  <span className='flex items-center gap-1.5'>
                    <CalendarIcon className='h-3.5 w-3.5' />
                    Created:{' '}
                    {new Date(federation.createdAt).toLocaleDateString(undefined, {
                      month: 'short',
                      day: 'numeric',
                      year: 'numeric',
                    })}
                  </span>
                  <span className='flex items-center gap-1.5'>
                    <ClockIcon className='h-3.5 w-3.5' />
                    Last synced:{' '}
                    {federation.lastSync?.finishedAt ? formatDistanceToNow(federation.lastSync.finishedAt) : 'Never'}
                  </span>
                </div>
              )}
              <MainConfigForm
                formData={formData}
                updateField={updateField}
                errors={errors}
                isEditMode={isEditMode}
                isReadOnly={isReadOnly}
                onTestConnection={handleTestConnection}
                testConnectionLoading={testConnectionLoading}
                testConnectionResult={testConnectionResult}
              />
            </>
          )}

          {isReadOnly && federation && (
            <div className='mt-8 border-t border-gray-200 dark:border-gray-700 pt-6'>
              <h3 className='text-lg font-medium text-gray-900 dark:text-white mb-4'>Discovered Resources</h3>
              <div className='grid grid-cols-3 gap-4'>
                <div className='bg-gray-50 dark:bg-gray-900 rounded-lg p-5 border border-gray-200 dark:border-gray-700 text-center'>
                  <div className='text-3xl font-bold text-purple-600 dark:text-purple-400'>
                    {federation.stats?.mcpServerCount || 0}
                  </div>
                  <div className='text-sm text-gray-500 mt-1'>MCP Servers</div>
                </div>
                <div className='bg-gray-50 dark:bg-gray-900 rounded-lg p-5 border border-gray-200 dark:border-gray-700 text-center'>
                  <div className='text-3xl font-bold text-emerald-600 dark:text-emerald-400'>
                    {federation.stats?.agentCount || 0}
                  </div>
                  <div className='text-sm text-gray-500 mt-1'>AI Agents</div>
                </div>
                <div className='bg-gray-50 dark:bg-gray-900 rounded-lg p-5 border border-gray-200 dark:border-gray-700 text-center'>
                  <div className='text-3xl font-bold text-blue-600 dark:text-blue-400'>
                    {federation.stats?.importedTotal || 0}
                  </div>
                  <div className='text-sm text-gray-500 mt-1'>Total Imported</div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className='px-6 py-4 border-t border-gray-100 dark:border-gray-700 flex flex-wrap items-center justify-between gap-4'>
          <div className='flex items-center gap-3'>
            {isEditMode && !isReadOnly && federation?.permissions?.DELETE && (
              <button
                onClick={handleDelete}
                disabled={loading}
                className='inline-flex items-center px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm font-medium text-red-500 dark:text-red-400 bg-white dark:bg-gray-800 hover:bg-red-50 dark:hover:bg-red-900/20 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 disabled:opacity-50 disabled:cursor-not-allowed'
              >
                <TrashIcon className='h-4 w-4' />
              </button>
            )}
            {isEditMode && !!id && federation?.permissions?.SHARE && (
              <button
                onClick={() => setShareOpen(true)}
                disabled={loading || loadingDetail}
                className='inline-flex items-center px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm font-medium text-purple-600 dark:text-purple-400 bg-white dark:bg-gray-800 hover:bg-purple-50 dark:hover:bg-purple-900/20 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed'
              >
                <HiOutlineShare className='h-4 w-4' />
              </button>
            )}
          </div>

          <div className='flex gap-3'>
            <button
              onClick={goBack}
              disabled={loading}
              className='min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm font-medium text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed'
            >
              {isReadOnly ? 'Back' : 'Cancel'}
            </button>

            {isReadOnly && (
              <button
                onClick={handleSync}
                disabled={loading || loadingDetail || isSyncing}
                className='inline-flex items-center justify-center gap-2 min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-purple-300 dark:border-purple-600 rounded-md shadow-sm text-sm font-medium text-purple-700 dark:text-purple-300 bg-white dark:bg-gray-800 hover:bg-purple-50 dark:hover:bg-purple-900/20 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed'
              >
                {isSyncing && (
                  <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-purple-700 dark:border-purple-300'></div>
                )}
                {!isSyncing && <ArrowPathIcon className='h-4 w-4' />}
                Sync Now
              </button>
            )}

            {!isReadOnly && (
              <button
                onClick={handleSave}
                disabled={loading}
                className='inline-flex items-center justify-center gap-2 min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-purple-700 hover:bg-purple-800 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed'
              >
                {loading && <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-white'></div>}
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
