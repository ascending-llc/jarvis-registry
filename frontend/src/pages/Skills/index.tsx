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
import type { RequestErrorPayload, ValidationIssue } from '@/services/request';
import type { SkillDetail, SkillMetadata } from '@/services/skill/type';

import { SKILL_MARKDOWN_PATH } from './constants';
import DeleteSkillDialog from './DeleteSkillDialog';
import SkillEditorView from './SkillEditorView';
import SkillListView from './SkillListView';
import {
  applySkillMarkdownInput,
  cloneDraft,
  createDraft,
  createEmptyDraft,
  getSkillDisplayName,
  metadataFromDetail,
  toCreateRequest,
  toUpdateRequest,
  updateSkillMarkdownMetadata,
  validateDraft,
} from './skillDraft';
import type { EditorMode, SkillDraft, SkillPageError, SkillStatusFilter } from './types';

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

const getUpdatedTimestamp = (updatedAt?: string | null): number => {
  if (!updatedAt) return 0;
  const timestamp = new Date(updatedAt).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
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
  const { skills, setSkills, skillStats, skillLoading, skillError, refreshSkillData } = useServer();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const skillId = searchParams.get('skill');
  const isCreate = !skillId && searchParams.get('create') === 'true';
  const isList = !skillId && !isCreate;

  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<SkillStatusFilter>('all');

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

  const detailRequestRef = useRef(0);
  const skipNextDetailLoadRef = useRef<string | null>(null);

  const loadSkillDetail = useCallback(async (id: string) => {
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
      setSelectedPath(SKILL_MARKDOWN_PATH);
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

  useEffect(() => {
    detailRequestRef.current += 1;
    setShareTarget(null);
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

  const filteredSkills = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLocaleLowerCase();
    return skills
      .filter(skill => {
        if (statusFilter === 'enabled' && !skill.enabled) return false;
        if (statusFilter === 'disabled' && skill.enabled) return false;
        if (!normalizedSearch) return true;
        return getSkillDisplayName(skill).toLocaleLowerCase().includes(normalizedSearch);
      })
      .sort((left, right) => getUpdatedTimestamp(right.updatedAt) - getUpdatedTimestamp(left.updatedAt));
  }, [searchTerm, skills, statusFilter]);

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

  const updateListFromDetail = (detail: SkillDetail) => {
    const metadata = metadataFromDetail(detail);
    setSkills(current => [metadata, ...current.filter(skill => skill.id !== metadata.id)]);
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

        const nextDraft = createDraft(finalDetail, draft.markdown.value);
        setSnapshot(cloneDraft(nextDraft));
        setDraft(nextDraft);
        updateListFromDetail(finalDetail);
        skipNextDetailLoadRef.current = finalDetail.id;
        navigateToSkill(finalDetail.id, true);
        if (toggleWarning) showToast(toggleWarning, 'error');
        else showToast('Skill created successfully.', 'success');
        return;
      }

      const updated = await SERVICES.SKILL.updateSkill(draft.id, toUpdateRequest(draft));
      const nextDraft = createDraft(updated, draft.markdown.value);
      setSnapshot(cloneDraft(nextDraft));
      setDraft(nextDraft);
      updateListFromDetail(updated);
      showToast('Skill updated successfully.', 'success');
    } catch (error) {
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

    const nextEnabled = !draft.enabled;
    setToggling(true);
    try {
      const result = await SERVICES.SKILL.toggleSkillState(draft.id, { enabled: nextEnabled });
      const updatedAt = new Date().toISOString();
      setDraft(current => (current ? { ...current, enabled: result.enabled } : current));
      setSnapshot(current => (current ? { ...current, enabled: result.enabled } : current));
      setSkills(current => {
        const updatedSkill = current.find(skill => skill.id === result.id);
        if (!updatedSkill) return current;
        return [
          { ...updatedSkill, enabled: result.enabled, updatedAt },
          ...current.filter(skill => skill.id !== result.id),
        ];
      });
    } catch (error) {
      handleMutationError(error, 'Failed to update skill status.');
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
  };

  return (
    <Layout searchConfig={searchConfig} skillsNavigation={skillsNavigation}>
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
          loading={skillLoading}
          refreshing={refreshing}
          error={skillError}
          hasActiveConditions={Boolean(searchTerm.trim()) || statusFilter !== 'all'}
          onRetry={() => void refreshSkillData()}
          onRefresh={() => void handleRefreshSkills()}
          onOpenSkill={id => navigateToSkill(id)}
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
        />
      )}
    </Layout>
  );
};

export default SkillsPage;
