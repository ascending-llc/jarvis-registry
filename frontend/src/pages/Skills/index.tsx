import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import Layout from '@/components/Layout';
import ShareModal from '@/components/ShareModal';
import { useAuth } from '@/contexts/AuthContext';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import { APP_ROUTES } from '@/routes';
import SERVICES from '@/services';
import type { ModelGatewaySelection } from '@/services/model/type';
import type { RequestErrorPayload, ValidationIssue } from '@/services/request';
import type { SkillScanResponse } from '@/services/skill/scanTypes';
import type { SkillMetadata } from '@/services/skill/type';
import type { SkillScanFilter } from '@/types/layout';

import { SKILL_MARKDOWN_PATH } from './constants';
import DeleteSkillDialog from './DeleteSkillDialog';
import SkillEditorView from './SkillEditorView';
import SkillListView from './SkillListView';
import SkillScanSettingsDialog from './SkillScanSettingsDialog';
import {
  applySkillMarkdownInput,
  cloneDraft,
  createDraft,
  createEmptyDraft,
  getSkillDisplayName,
  getSkillMarkdownInput,
  metadataFromDetail,
  toCreateRequest,
  toUpdateRequest,
  updateSkillMarkdownMetadata,
  validateDraft,
} from './skillDraft';
import {
  DEFAULT_SKILL_SORT,
  getNextSkillSort,
  matchesSkillScanFilter,
  matchesSkillSearch,
  SKILL_SCAN_FILTERS,
  type SkillSort,
  sortSkills,
} from './skillListQuery';
import { isScanActive, SKILL_SCAN_POLL_INTERVAL_MS, type SkillScanTarget, toScanBrief } from './skillScan';
import type { EditorMode, SkillDraft, SkillPageError, SkillStatusFilter } from './types';
import useSkillScan from './useSkillScan';

const getRequestError = (error: unknown): RequestErrorPayload => {
  if (!error || typeof error !== 'object') {
    return { detail: error instanceof Error ? error.message : 'Unexpected error.' };
  }
  return error as RequestErrorPayload;
};

const formatValidationIssues = (issues: ValidationIssue[]): string =>
  issues.map(issue => `${issue.loc[issue.loc.length - 1] ?? 'Field'}: ${issue.msg}`).join(' ');

const getRequestErrorMessage = (error: unknown, fallback: string): string => {
  const detail = getRequestError(error).detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) return formatValidationIssues(detail);
  return fallback;
};

const toPageError = (error: unknown): SkillPageError => {
  const requestError = getRequestError(error);
  if (requestError.httpStatus === 403) {
    return { kind: 'forbidden', message: getRequestErrorMessage(error, 'You cannot view this skill.') };
  }
  if (requestError.httpStatus === 404) {
    return { kind: 'not-found', message: getRequestErrorMessage(error, 'This skill no longer exists.') };
  }
  return { kind: 'generic', message: getRequestErrorMessage(error, 'Failed to load skill.') };
};

const removeSkillDeletePermission = (skill: SkillMetadata): SkillMetadata => {
  if (!skill.permissions) return skill;
  return {
    ...skill,
    permissions: { ...skill.permissions, DELETE: false },
  };
};

const SkillsPage: React.FC = () => {
  const { user } = useAuth();
  const { showToast } = useGlobal();
  const {
    skills,
    setSkills,
    skillStats,
    skillLoading,
    skillError,
    refreshSkillData,
    updateSkillMetadata,
    updateSkillScanSummary,
    setModelSelection,
  } = useServer();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get('tab') === 'security' ? 'security' : 'files';
  const skillId = searchParams.get('skill');
  const isCreate = !skillId && searchParams.get('create') === 'true';
  const isList = !skillId && !isCreate;

  const [searchTerm, setSearchTerm] = useState('');
  const [sort, setSort] = useState<SkillSort>(DEFAULT_SKILL_SORT);
  const [statusFilter, setStatusFilter] = useState<SkillStatusFilter>('all');
  const [scanFilter, setScanFilter] = useState<SkillScanFilter>('all');

  const [snapshot, setSnapshot] = useState<SkillDraft | null>(null);
  const [draft, setDraft] = useState<SkillDraft | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<SkillPageError | null>(null);
  const [selectedPath, setSelectedPath] = useState(SKILL_MARKDOWN_PATH);
  const [editorMode, setEditorMode] = useState<EditorMode>('preview');
  const [saving, setSaving] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [shareTarget, setShareTarget] = useState<SkillMetadata | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SkillMetadata | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [scanSettingsOpen, setScanSettingsOpen] = useState(false);
  const [scanTarget, setScanTarget] = useState<SkillScanTarget | null>(null);
  const [selectedFindingKey, setSelectedFindingKey] = useState<string | null>(null);
  const hasUnsavedChanges = Boolean(
    draft &&
      snapshot &&
      (getSkillMarkdownInput(draft.markdown) !== getSkillMarkdownInput(snapshot.markdown) ||
        draft.category !== snapshot.category ||
        draft.alwaysApply !== snapshot.alwaysApply),
  );
  const canRequestScan = Boolean(
    user?.scopes?.includes('skills-write') && draft?.id === skillId && draft?.permissions?.EDIT,
  );
  const canConfigureScan = Boolean(user?.scopes?.includes('models-write') && user?.scopes?.includes('models-read'));
  const handleScanChange = useCallback(
    (scan: SkillScanResponse) => {
      updateSkillScanSummary(scan.skillId, scan.skillVersion, toScanBrief(scan));
    },
    [updateSkillScanSummary],
  );
  const scanState = useSkillScan({
    skillId,
    skillVersion: draft?.id === skillId ? (draft?.version ?? null) : null,
    enabled: Boolean(skillId && draft?.id === skillId && !detailError),
    canRequestScan: canRequestScan && !hasUnsavedChanges && !saving && !toggling,
    onScanChange: handleScanChange,
  });
  const reportIdentity = `${scanState.scan?.summary?.contentDigest ?? ''}:${scanState.scan?.summary?.scannedAt ?? ''}`;
  useEffect(() => {
    setScanTarget(null);
    setSelectedFindingKey(null);
  }, [skillId, reportIdentity]);

  const changeTab = (tab: 'files' | 'security') => {
    const next = new URLSearchParams(searchParams);
    next.set('tab', tab);
    setSearchParams(next);
  };
  const handleLocateFinding = (target: SkillScanTarget) => {
    setScanTarget(target);
    setSelectedFindingKey(null);
    setSelectedPath(target.path);
    setEditorMode('preview');
    changeTab('files');
  };
  const handleShowFinding = (key: string) => {
    setSelectedFindingKey(key);
    changeTab('security');
  };
  const handleRequestScan = async () => {
    const result = await scanState.requestScan();
    if (result?.httpStatus === 202) showToast('Scan request accepted.', 'success');
    if (result?.httpStatus === 200) showToast('Current scan state loaded. No new scan was queued.', 'info');
  };
  const handleScanSettingsSaved = (selection: ModelGatewaySelection) => {
    setModelSelection(selection);
    showToast('Scan settings saved.', 'success');
    void refreshSkillData(true, { afterWrite: true });
    if (skillId) void scanState.refresh();
  };

  const detailRequestRef = useRef(0);
  const skipNextDetailLoadRef = useRef<string | null>(null);

  const loadSkillDetail = useCallback(async (id: string, preserveFile = false) => {
    const requestId = detailRequestRef.current + 1;
    detailRequestRef.current = requestId;
    setDetailLoading(true);
    setDetailError(null);
    try {
      const detail = await SERVICES.SKILL.getSkillDetail(id);
      if (detailRequestRef.current !== requestId) return;
      const nextDraft = createDraft(detail);
      setSnapshot(cloneDraft(nextDraft));
      setDraft(nextDraft);
      setSelectedPath(current =>
        preserveFile && detail.files.some(file => file.relativePath === current) ? current : SKILL_MARKDOWN_PATH,
      );
      setEditorMode('preview');
    } catch (error) {
      if (detailRequestRef.current !== requestId) return;
      setDraft(null);
      setSnapshot(null);
      setDetailError(toPageError(error));
    } finally {
      if (detailRequestRef.current === requestId) setDetailLoading(false);
    }
  }, []);

  const handleRefreshContent = async () => {
    if (!skillId || hasUnsavedChanges || saving || toggling) return;
    setScanTarget(null);
    await loadSkillDetail(skillId, true);
    await scanState.refresh();
  };

  useEffect(() => {
    detailRequestRef.current += 1;
    setShareTarget(null);
    setScanSettingsOpen(false);
    setDeleteTarget(null);
    setDeleting(false);

    if (skillId && skipNextDetailLoadRef.current === skillId) {
      skipNextDetailLoadRef.current = null;
      setDetailLoading(false);
      setDetailError(null);
      return;
    }

    setSelectedPath(SKILL_MARKDOWN_PATH);

    if (skillId) {
      void loadSkillDetail(skillId);
      return;
    }

    if (isCreate) {
      const emptyDraft = createEmptyDraft(user?.username ?? 'You');
      setSnapshot(cloneDraft(emptyDraft));
      setDraft(emptyDraft);
      setDetailLoading(false);
      setDetailError(null);
      setEditorMode('edit');
      return;
    }

    setDraft(null);
    setSnapshot(null);
    setDetailLoading(false);
    setDetailError(null);
  }, [isCreate, loadSkillDetail, skillId, user?.username]);

  const matchedSkills = useMemo(
    () =>
      skills
        .filter(skill => {
          if (statusFilter === 'enabled' && !skill.enabled) return false;
          if (statusFilter === 'disabled' && skill.enabled) return false;
          return true;
        })
        .filter(skill => matchesSkillSearch(skill, searchTerm)),
    [searchTerm, skills, statusFilter],
  );
  const filteredSkills = useMemo(
    () =>
      sortSkills(
        matchedSkills.filter(skill => matchesSkillScanFilter(skill, scanFilter)),
        sort,
      ),
    [matchedSkills, scanFilter, sort],
  );
  const scanFilterItems = useMemo(
    () =>
      SKILL_SCAN_FILTERS.map(item => ({
        ...item,
        count: matchedSkills.filter(skill => matchesSkillScanFilter(skill, item.value)).length,
      })),
    [matchedSkills],
  );

  useEffect(() => {
    if (!isList) return;
    const refreshVisibleList = () => {
      if (!document.hidden) void refreshSkillData(true);
    };
    refreshVisibleList();
    document.addEventListener('visibilitychange', refreshVisibleList);
    return () => document.removeEventListener('visibilitychange', refreshVisibleList);
  }, [isList, refreshSkillData]);

  const hasActiveScans = skills.some(skill => isScanActive(skill.scanSummary?.scanStatus));
  useEffect(() => {
    if (!isList || !hasActiveScans || skillError || refreshing) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      clearTimeout(timer);
      if (disposed || document.hidden) return;
      timer = setTimeout(async () => {
        await refreshSkillData(true);
        schedule();
      }, SKILL_SCAN_POLL_INTERVAL_MS);
    };
    schedule();
    document.addEventListener('visibilitychange', schedule);
    return () => {
      disposed = true;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', schedule);
    };
  }, [isList, hasActiveScans, skillError, refreshing, refreshSkillData]);

  const navigateToList = () => navigate(APP_ROUTES.skills);
  const navigateToCreate = () => navigate(`${APP_ROUTES.skills}?create=true`);
  const navigateToSkill = (id: string, replace = false) =>
    navigate(`${APP_ROUTES.skills}?skill=${encodeURIComponent(id)}`, { replace });

  const handleRefreshSkills = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      await refreshSkillData(true);
    } finally {
      setRefreshing(false);
    }
  };

  const handleSelectFile = (path: string) => {
    setScanTarget(null);
    setSelectedPath(path);
    if (path !== SKILL_MARKDOWN_PATH) setEditorMode('preview');
  };

  const handleMarkdownChange = (markdown: string) => {
    setDraft(current => {
      if (!current) return current;
      return { ...current, markdown: applySkillMarkdownInput(current.markdown, markdown) };
    });
  };

  const handleNameChange = (displayTitle: string) => {
    setDraft(current => {
      if (!current) return current;
      return { ...current, markdown: updateSkillMarkdownMetadata(current.markdown, { displayTitle }) };
    });
  };

  const handleDescriptionChange = (description: string) => {
    setDraft(current => {
      if (!current) return current;
      return { ...current, markdown: updateSkillMarkdownMetadata(current.markdown, { description }) };
    });
  };

  const handleCategoryChange = (category: string) => {
    setDraft(current => (current ? { ...current, category } : current));
  };

  const makeDraftReadOnly = () => {
    setDraft(current => {
      if (!current) return current;
      const permissions = current.permissions ?? { VIEW: true, EDIT: false, DELETE: false, SHARE: false };
      return { ...current, permissions: { ...permissions, EDIT: false } };
    });
  };

  const handleMutationError = (error: unknown, fallback: string) => {
    const requestError = getRequestError(error);
    if (requestError.httpStatus === 403) {
      makeDraftReadOnly();
      showToast('You no longer have permission to edit this skill.', 'error');
      return;
    }
    if (requestError.httpStatus === 404) {
      setDetailError({ kind: 'not-found', message: 'This skill no longer exists.' });
      showToast('This skill no longer exists.', 'error');
      return;
    }
    if (requestError.httpStatus === 409) {
      showToast('A skill with this name already exists.', 'error');
      return;
    }
    showToast(getRequestErrorMessage(error, fallback), 'error');
  };

  const handleSave = async () => {
    if (!draft || saving || toggling) return;
    const validation = validateDraft(draft);
    if (!validation.valid) {
      showToast(validation.message, 'error');
      return;
    }

    const saveRequestId = detailRequestRef.current;
    setSaving(true);
    try {
      if (draft.id === null) {
        const created = await SERVICES.SKILL.createSkill(toCreateRequest(draft));
        let finalDetail = created;
        let toggleWarning: string | null = null;

        if (!draft.enabled) {
          try {
            const toggleResult = await SERVICES.SKILL.toggleSkillState(created.id, { enabled: false });
            finalDetail = { ...created, enabled: toggleResult.enabled };
          } catch (error) {
            toggleWarning = `Skill was created, but its status could not be changed: ${getRequestErrorMessage(
              error,
              'status update failed',
            )}`;
          }
        }

        updateSkillMetadata(metadataFromDetail(finalDetail));
        void refreshSkillData(true);
        if (saveRequestId !== detailRequestRef.current) return;
        const nextDraft = createDraft(finalDetail, draft.markdown.value);
        setSnapshot(cloneDraft(nextDraft));
        setDraft(nextDraft);
        skipNextDetailLoadRef.current = finalDetail.id;
        navigateToSkill(finalDetail.id, true);
        if (toggleWarning) showToast(toggleWarning, 'error');
        else showToast('Skill created. View scan updates in Security.', 'success');
        return;
      }

      const updated = await SERVICES.SKILL.updateSkill(draft.id, toUpdateRequest(draft));
      updateSkillMetadata(metadataFromDetail(updated));
      void refreshSkillData(true);
      if (saveRequestId !== detailRequestRef.current) return;
      const nextDraft = createDraft(updated, draft.markdown.value);
      setSnapshot(cloneDraft(nextDraft));
      setDraft(nextDraft);
      void scanState.refresh(updated.version);
      showToast('Skill saved. View scan updates in Security.', 'success');
    } catch (error) {
      if (saveRequestId === detailRequestRef.current)
        handleMutationError(error, 'Failed to save skill. Your draft has been preserved.');
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async () => {
    if (!draft || toggling || saving) return;
    if (draft.id === null) {
      setDraft(current => (current ? { ...current, enabled: !current.enabled } : current));
      return;
    }

    const toggleRequestId = detailRequestRef.current;
    const nextEnabled = !draft.enabled;
    setToggling(true);
    try {
      const result = await SERVICES.SKILL.toggleSkillState(draft.id, { enabled: nextEnabled });
      const updatedAt = new Date().toISOString();
      setDraft(current => (current?.id === result.id ? { ...current, enabled: result.enabled } : current));
      setSnapshot(current => (current?.id === result.id ? { ...current, enabled: result.enabled } : current));
      setSkills(current => {
        const updatedSkill = current.find(skill => skill.id === result.id);
        if (!updatedSkill) return current;
        return [
          { ...updatedSkill, enabled: result.enabled, updatedAt },
          ...current.filter(skill => skill.id !== result.id),
        ];
      });
      void refreshSkillData(true);
    } catch (error) {
      if (toggleRequestId === detailRequestRef.current) handleMutationError(error, 'Failed to update skill status.');
    } finally {
      setToggling(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget || deleting) return;

    const deletedSkillId = deleteTarget.id;
    setDeleting(true);
    try {
      await SERVICES.SKILL.deleteSkill(deletedSkillId);
      setDeleteTarget(null);
      setSkills(current => current.filter(skill => skill.id !== deletedSkillId));
      showToast('Skill deleted successfully.', 'success');
      await refreshSkillData(true);
    } catch (error) {
      const requestError = getRequestError(error);
      if (requestError.httpStatus === 403) {
        setSkills(current =>
          current.map(skill => (skill.id === deletedSkillId ? removeSkillDeletePermission(skill) : skill)),
        );
        setDeleteTarget(null);
        showToast('You no longer have permission to delete this skill.', 'error');
        return;
      }
      if (requestError.httpStatus === 404) {
        setDeleteTarget(null);
        setSkills(current => current.filter(skill => skill.id !== deletedSkillId));
        showToast('This skill no longer exists.', 'error');
        await refreshSkillData(true);
        return;
      }
      showToast(getRequestErrorMessage(error, 'Failed to delete skill.'), 'error');
    } finally {
      setDeleting(false);
    }
  };

  const handleReset = () => {
    if (!snapshot) return;
    setDraft(cloneDraft(snapshot));
    setSelectedPath(SKILL_MARKDOWN_PATH);
    setEditorMode(snapshot.id === null ? 'edit' : 'preview');
  };

  const searchConfig = isList
    ? {
        value: searchTerm,
        placeholder: 'Search skills...',
        onChange: setSearchTerm,
        onClear: () => setSearchTerm(''),
      }
    : undefined;

  const skillsNavigation = {
    stats: skillStats,
    activeFilter: statusFilter,
    onFilterChange: setStatusFilter,
    showStatusFilters: isList,
    scanFilters: { active: scanFilter, items: scanFilterItems, onChange: setScanFilter },
  };

  return (
    <Layout searchConfig={searchConfig} skillsNavigation={skillsNavigation}>
      {canConfigureScan && (
        <SkillScanSettingsDialog
          isOpen={scanSettingsOpen}
          onClose={() => setScanSettingsOpen(false)}
          onSaved={handleScanSettingsSaved}
        />
      )}
      {shareTarget && (
        <ShareModal
          itemName={getSkillDisplayName(shareTarget)}
          resourceId={shareTarget.id}
          resourceType='skill'
          isOpen={true}
          onClose={() => setShareTarget(null)}
        />
      )}
      {deleteTarget && (
        <DeleteSkillDialog
          isOpen={true}
          skillName={getSkillDisplayName(deleteTarget)}
          deleting={deleting}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => void handleDelete()}
        />
      )}
      {isList ? (
        <SkillListView
          skills={filteredSkills}
          sort={sort}
          onSortChange={column => setSort(current => getNextSkillSort(current, column))}
          loading={skillLoading}
          refreshing={refreshing}
          error={skillError}
          hasActiveConditions={Boolean(searchTerm.trim()) || statusFilter !== 'all' || scanFilter !== 'all'}
          activeScanFilter={
            scanFilter === 'all' ? undefined : scanFilterItems.find(item => item.value === scanFilter)?.label
          }
          onClearScanFilter={() => setScanFilter('all')}
          onRetry={() => void refreshSkillData()}
          onRefresh={() => void handleRefreshSkills()}
          onOpenSkill={id => navigateToSkill(id)}
          onOpenSecurity={id => navigate(`${APP_ROUTES.skills}?skill=${encodeURIComponent(id)}&tab=security`)}
          onOpenScanSettings={() => setScanSettingsOpen(true)}
          canConfigureScan={canConfigureScan}
          onCreate={navigateToCreate}
          onShare={setShareTarget}
          onDelete={setDeleteTarget}
        />
      ) : (
        <SkillEditorView
          draft={draft}
          loading={detailLoading}
          error={detailError}
          selectedPath={selectedPath}
          editorMode={editorMode}
          saving={saving}
          toggling={toggling}
          onBack={navigateToList}
          onRetry={() => skillId && void loadSkillDetail(skillId)}
          onSelectFile={handleSelectFile}
          onEditorModeChange={setEditorMode}
          onNameChange={handleNameChange}
          onDescriptionChange={handleDescriptionChange}
          onMarkdownChange={handleMarkdownChange}
          onCategoryChange={handleCategoryChange}
          onToggle={() => void handleToggle()}
          onReset={handleReset}
          onSave={() => void handleSave()}
          activeTab={isCreate ? 'files' : activeTab}
          onTabChange={changeTab}
          scanState={scanState}
          canRequestScan={canRequestScan}
          hasUnsavedChanges={hasUnsavedChanges}
          scanTarget={scanTarget}
          selectedFindingKey={selectedFindingKey}
          onLocateFinding={handleLocateFinding}
          onShowFinding={handleShowFinding}
          onRequestScan={() => void handleRequestScan()}
          onRefreshContent={() => void handleRefreshContent()}
        />
      )}
    </Layout>
  );
};

export default SkillsPage;
