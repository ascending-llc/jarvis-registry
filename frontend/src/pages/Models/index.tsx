import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import Layout from '@/components/Layout';
import { useAuth } from '@/contexts/AuthContext';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import { APP_ROUTES } from '@/routes';
import SERVICES from '@/services';
import type {
  EmbeddingReindexJob,
  ModelSourceDetail,
  ModelSourceListItem,
  ModelSourcePagination,
} from '@/services/model/type';
import type { ModelProviderFilter } from '@/types/layout';
import { getErrorMessage } from '@/utils/getErrorMessage';

import DeleteModelDialog from './DeleteModelDialog';
import ModelDetailView from './ModelDetailView';
import ModelFormView from './ModelFormView';
import ModelListView from './ModelListView';
import { createEmptyModelForm, modelDetailToForm, toCreateModelRequest, toUpdateModelRequest } from './modelForm';
import type { ModelDetailError, ModelFormState } from './types';

const PAGE_SIZE = 20;

const getHttpStatus = (error: unknown): number | undefined => {
  if (!error || typeof error !== 'object' || !('httpStatus' in error)) return undefined;
  const { httpStatus } = error as { httpStatus?: unknown };
  return typeof httpStatus === 'number' ? httpStatus : undefined;
};

const getDetailError = (error: unknown): ModelDetailError => {
  const status = getHttpStatus(error);
  if (status === 403) return { kind: 'forbidden', message: getErrorMessage(error, 'You cannot view this model.') };
  if (status === 404) return { kind: 'not-found', message: getErrorMessage(error, 'This model no longer exists.') };
  return { kind: 'generic', message: getErrorMessage(error, 'Failed to load model.') };
};

const ModelsPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const { showToast } = useGlobal();
  const {
    models,
    setModels,
    modelStats,
    modelLoading,
    modelError,
    modelSelection,
    setModelSelection,
    modelSelectionError,
    refreshModelData,
  } = useServer();

  const modelId = searchParams.get('id');
  const isCreate = !modelId && searchParams.get('create') === 'true';
  const isEdit = Boolean(modelId) && searchParams.get('edit') === 'true';
  const isDetail = Boolean(modelId) && !isEdit;
  const isList = !modelId && !isCreate;

  const [listRefreshing, setListRefreshing] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [providerFilter, setProviderFilter] = useState<ModelProviderFilter>('all');
  const [page, setPage] = useState(1);

  const [selectedModel, setSelectedModel] = useState<ModelSourceDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<ModelDetailError | null>(null);
  const [reindexJob, setReindexJob] = useState<EmbeddingReindexJob | null>(null);
  const [settingDefaultId, setSettingDefaultId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const detailRequestRef = useRef(0);
  const canWrite = Boolean(user?.isAdmin || user?.scopes?.includes('models-write'));

  const filteredModels = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLocaleLowerCase();
    return models.filter(model => {
      if (providerFilter !== 'all' && model.providerType !== providerFilter) return false;
      if (!normalizedSearch) return true;
      return `${model.displayName} ${model.description ?? ''}`.toLocaleLowerCase().includes(normalizedSearch);
    });
  }, [models, providerFilter, searchTerm]);
  const totalPages = Math.ceil(filteredModels.length / PAGE_SIZE);
  const currentPage = Math.min(page, Math.max(1, totalPages));
  const pagination: ModelSourcePagination = {
    total: filteredModels.length,
    page: currentPage,
    perPage: PAGE_SIZE,
    totalPages,
  };
  const visibleModels = filteredModels.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const navigateToList = useCallback(() => navigate(APP_ROUTES.models), [navigate]);
  const navigateToCreate = () => navigate(`${APP_ROUTES.models}?create=true`);
  const navigateToDetail = useCallback(
    (id: string) => navigate(`${APP_ROUTES.models}?id=${encodeURIComponent(id)}`),
    [navigate],
  );
  const navigateToEdit = (id: string) => navigate(`${APP_ROUTES.models}?id=${encodeURIComponent(id)}&edit=true`);

  const loadLatestReindexJob = useCallback(async (includeTerminal = false) => {
    try {
      const result = await SERVICES.MODEL.getEmbeddingReindexJobs(1);
      const latest = result.jobs[0] ?? null;
      setReindexJob(latest?.status === 'running' || includeTerminal ? latest : null);
      return latest;
    } catch (_error) {
      if (!includeTerminal) setReindexJob(null);
      return null;
    }
  }, []);

  const loadModelDetail = useCallback(async (id: string) => {
    const requestId = detailRequestRef.current + 1;
    detailRequestRef.current = requestId;
    setDetailLoading(true);
    setDetailError(null);
    try {
      const result = await SERVICES.MODEL.getModelSourceDetail(id);
      if (detailRequestRef.current !== requestId) return;
      setSelectedModel(result);
    } catch (error) {
      if (detailRequestRef.current !== requestId) return;
      setSelectedModel(null);
      setDetailError(getDetailError(error));
    } finally {
      if (detailRequestRef.current === requestId) setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadLatestReindexJob(true);
  }, [loadLatestReindexJob]);

  useEffect(() => {
    if (modelLoading || reindexJob?.status !== 'completed') return;
    if (modelSelection.embeddingModelSourceId === reindexJob.targetEmbeddingModelSourceId) return;
    void refreshModelData(true);
  }, [
    modelLoading,
    modelSelection.embeddingModelSourceId,
    refreshModelData,
    reindexJob?.status,
    reindexJob?.targetEmbeddingModelSourceId,
  ]);

  useEffect(() => {
    detailRequestRef.current += 1;
    setDeleteDialogOpen(false);
    setSelectedModel(null);
    setDetailError(null);
    if (!modelId) {
      setDetailLoading(false);
      return;
    }
    void loadModelDetail(modelId);
  }, [isEdit, loadModelDetail, modelId]);

  useEffect(() => {
    if (isList) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (deleteDialogOpen) {
          if (!deleting) setDeleteDialogOpen(false);
          return;
        }
        if (saving || deleting) return;
        navigateToList();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [deleteDialogOpen, deleting, isList, navigateToList, saving]);

  useEffect(() => {
    if (reindexJob?.status !== 'running') return undefined;
    const interval = window.setInterval(async () => {
      const latest = await loadLatestReindexJob(true);
      if (!latest || latest.status === 'running') return;
      window.clearInterval(interval);
      await refreshModelData(true);
      if (latest.status === 'completed') {
        showToast('Embedding model is active and the catalog reindex is complete.', 'success');
      } else {
        showToast(latest.error || latest.lastError || 'Embedding reindex failed.', 'error');
      }
    }, 3000);
    return () => window.clearInterval(interval);
  }, [loadLatestReindexJob, refreshModelData, reindexJob?.status, showToast]);

  const handleProviderFilterChange = (provider: ModelProviderFilter) => {
    setPage(1);
    setProviderFilter(provider);
  };

  const handleSearchChange = (value: string) => {
    setPage(1);
    setSearchTerm(value);
  };

  const handleRefresh = async () => {
    if (listRefreshing) return;
    setListRefreshing(true);
    try {
      await Promise.all([refreshModelData(true), loadLatestReindexJob(true)]);
    } finally {
      setListRefreshing(false);
    }
  };

  const handleSetDefault = async (model: Pick<ModelSourceListItem, 'id' | 'mode'>) => {
    if (!canWrite || settingDefaultId) return;
    if (model.mode === 'embedding' && reindexJob?.status === 'running') {
      showToast('An embedding reindex is already running.', 'error');
      return;
    }

    setSettingDefaultId(model.id);
    try {
      if (model.mode === 'chat') {
        const nextSelection = await SERVICES.MODEL.setDefaultWorkflowModel(model.id);
        setModelSelection(nextSelection);
        await refreshModelData(true);
        showToast('Default workflow model updated.', 'success');
        return;
      }
      await SERVICES.MODEL.setEmbeddingModel(model.id);
      const latest = await loadLatestReindexJob(true);
      if (latest?.status === 'completed') await refreshModelData(true);
      showToast('Smoke test passed. Catalog reindex started.', 'info');
    } catch (error) {
      showToast(getErrorMessage(error, `Failed to set the default ${model.mode} model.`), 'error');
    } finally {
      setSettingDefaultId(null);
    }
  };

  const handleSave = async (form: ModelFormState) => {
    if (!canWrite || saving) return;
    setSaving(true);
    try {
      if (isEdit && modelId) {
        const request = toUpdateModelRequest(form);
        if (!request) {
          showToast('Backend support for this provider is not available yet.', 'info');
          return;
        }
        await SERVICES.MODEL.updateModelSource(modelId, request);
        await refreshModelData(true);
        showToast('Model updated successfully.', 'success');
        navigateToDetail(modelId);
        return;
      }

      const request = toCreateModelRequest(form);
      if (!request) {
        showToast('Backend support for this provider is not available yet.', 'info');
        return;
      }
      const created = await SERVICES.MODEL.createModelSource(request);
      await refreshModelData(true);
      showToast('Model created successfully.', 'success');
      navigateToDetail(created.id);
    } catch (error) {
      showToast(getErrorMessage(error, isEdit ? 'Failed to update model.' : 'Failed to create model.'), 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!modelId || !canWrite || deleting) return;
    setDeleting(true);
    try {
      await SERVICES.MODEL.deleteModelSource(modelId);
      setModels(current => current.filter(model => model.id !== modelId));
      setDeleteDialogOpen(false);
      navigateToList();
      showToast('Model deleted successfully.', 'success');
      await refreshModelData(true);
    } catch (error) {
      if (getHttpStatus(error) === 404) {
        setModels(current => current.filter(model => model.id !== modelId));
        navigateToList();
      }
      setDeleteDialogOpen(false);
      showToast(getErrorMessage(error, 'Failed to delete model.'), 'error');
    } finally {
      setDeleting(false);
    }
  };

  const searchConfig = isList
    ? {
        value: searchTerm,
        placeholder: 'Search models...',
        onChange: handleSearchChange,
        onClear: () => handleSearchChange(''),
      }
    : undefined;

  const modelsNavigation = {
    total: modelStats.total,
    providerCounts: {
      all: modelStats.total,
      aws_bedrock: modelStats.aws_bedrock,
      azure_openai: modelStats.azure_openai,
    },
    activeProvider: providerFilter,
    onProviderChange: handleProviderFilterChange,
    showProviderFilters: isList,
  };

  const activeModel = selectedModel?.id === modelId ? selectedModel : null;
  const formState = activeModel ? modelDetailToForm(activeModel) : createEmptyModelForm();
  const detailIsLoading = Boolean(modelId) && !detailError && !activeModel;

  return (
    <Layout searchConfig={searchConfig} modelsNavigation={modelsNavigation}>
      <DeleteModelDialog
        isOpen={deleteDialogOpen}
        modelName={selectedModel?.displayName ?? 'this model'}
        deleting={deleting}
        onCancel={() => setDeleteDialogOpen(false)}
        onConfirm={() => void handleDelete()}
      />
      {isList ? (
        <ModelListView
          models={visibleModels}
          pagination={pagination}
          selection={modelSelection}
          selectionError={modelSelectionError}
          reindexJob={reindexJob}
          loading={modelLoading}
          refreshing={listRefreshing}
          error={modelError}
          hasActiveConditions={Boolean(searchTerm.trim()) || providerFilter !== 'all'}
          canWrite={canWrite}
          settingDefaultId={settingDefaultId}
          onAdd={navigateToCreate}
          onOpenModel={navigateToDetail}
          onEdit={navigateToEdit}
          onRefresh={() => void handleRefresh()}
          onRetry={() => void refreshModelData()}
          onPageChange={setPage}
          onSetDefault={model => void handleSetDefault(model)}
        />
      ) : isCreate ? (
        <ModelFormView
          key='create-model'
          initialForm={formState}
          editing={false}
          saving={saving}
          canWrite={canWrite}
          hasStoredApiKey={false}
          selection={modelSelection}
          onCancel={navigateToList}
          onSubmit={form => void handleSave(form)}
        />
      ) : isDetail ? (
        <ModelDetailView
          model={activeModel}
          loading={detailIsLoading || detailLoading}
          error={detailError}
          selection={modelSelection}
          selectionError={modelSelectionError}
          canWrite={canWrite}
          onBack={navigateToList}
          onEdit={() => modelId && navigateToEdit(modelId)}
          onRetry={() => modelId && void loadModelDetail(modelId)}
        />
      ) : (
        <ModelFormView
          key={activeModel ? `edit-loaded-${activeModel.id}` : `edit-loading-${modelId ?? 'unknown'}`}
          initialForm={formState}
          editing
          loading={detailIsLoading || detailLoading}
          loadError={detailError?.message}
          saving={saving}
          deleting={deleting}
          canWrite={canWrite}
          defaultModel={activeModel}
          selection={modelSelection}
          hasStoredApiKey={
            activeModel?.providerConfig.providerType === 'azure_openai' && activeModel.providerConfig.hasApiKey
          }
          onCancel={navigateToList}
          onDelete={() => setDeleteDialogOpen(true)}
          onRetry={() => modelId && void loadModelDetail(modelId)}
          onSubmit={form => void handleSave(form)}
        />
      )}
    </Layout>
  );
};

export default ModelsPage;
