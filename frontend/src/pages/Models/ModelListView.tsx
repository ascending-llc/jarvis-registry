import {
  ArrowPathIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CpuChipIcon,
  PencilSquareIcon,
  PlusIcon,
  StarIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';

import IconButton from '@/components/IconButton';
import type {
  EmbeddingReindexJob,
  ModelGatewaySelection,
  ModelSourceListItem,
  ModelSourcePagination,
} from '@/services/model/type';
import formatTimeSince from '@/utils/formatTimeSince';

import { MODE_BADGE_CLASSES, MODE_LABELS, PROVIDER_BADGE_CLASSES, PROVIDER_LABELS } from './constants';
import ModelDefaultBadge from './ModelDefaultBadge';

type ModelListViewProps = {
  models: ModelSourceListItem[];
  pagination: ModelSourcePagination;
  selection: ModelGatewaySelection;
  selectionError: string | null;
  reindexJob: EmbeddingReindexJob | null;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  hasActiveConditions: boolean;
  canWrite: boolean;
  settingDefaultId: string | null;
  onAdd: () => void;
  onOpenModel: (id: string) => void;
  onEdit: (id: string) => void;
  onRefresh: () => void;
  onRetry: () => void;
  onPageChange: (page: number) => void;
  onSetDefault: (model: ModelSourceListItem) => void;
};

const ReindexBanner: React.FC<{ job: EmbeddingReindexJob }> = ({ job }) => {
  if (job.status === 'failed') {
    return (
      <div className='mb-5 rounded-lg border border-[var(--jarvis-danger)]/30 bg-[var(--jarvis-danger-soft)] px-4 py-3 text-sm text-[var(--jarvis-danger-text)]'>
        <strong>Embedding reindex failed.</strong> {job.error || job.lastError || 'Review the latest reindex job.'}
      </div>
    );
  }
  if (job.status !== 'running') return null;
  return (
    <div className='mb-5 flex items-center gap-3 rounded-lg border border-[var(--jarvis-warning)]/30 bg-[var(--jarvis-warning-soft)] px-4 py-3 text-sm text-[var(--jarvis-warning-text)]'>
      <ArrowPathIcon className='h-4 w-4 flex-shrink-0 animate-spin' />
      <div>
        <strong>Re-embedding the catalog.</strong> Search remains available; writes to servers and agents may return 503
        until the switch completes.
      </div>
    </div>
  );
};

const ModelListView: React.FC<ModelListViewProps> = ({
  models,
  pagination,
  selection,
  selectionError,
  reindexJob,
  loading,
  refreshing,
  error,
  hasActiveConditions,
  canWrite,
  settingDefaultId,
  onAdd,
  onOpenModel,
  onEdit,
  onRefresh,
  onRetry,
  onPageChange,
  onSetDefault,
}) => (
  <section className='flex min-h-0 flex-1 flex-col overflow-y-auto px-0 pt-2 md:px-8'>
    <div className='mb-2 flex flex-shrink-0 items-start justify-between gap-4'>
      <div className='min-w-0 flex-1'>
        <h1 className='text-2xl font-bold tracking-[-0.01em] text-[var(--jarvis-text-strong)]'>Models</h1>
        <p className='mb-7 mt-2 text-sm leading-[1.6] text-[var(--jarvis-muted)]'>
          Cloud-hosted language and embedding models served through the Jarvis gateway.
        </p>
      </div>

      <div className='flex flex-shrink-0 items-center gap-3'>
        <IconButton
          ariaLabel='Refresh models'
          tooltip='Refresh'
          onClick={onRefresh}
          disabled={refreshing}
          spinning={refreshing}
          className='flex h-10 w-10 items-center justify-center rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-surface)] text-[var(--jarvis-text)] transition-colors hover:bg-[var(--jarvis-card-muted)]'
        >
          <ArrowPathIcon className='h-4 w-4' />
        </IconButton>
        <IconButton
          ariaLabel='Add model'
          tooltip={canWrite ? 'Add model' : 'Requires models-write scope'}
          onClick={onAdd}
          disabled={!canWrite}
          variant='solid'
          className='flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--jarvis-primary)] text-white shadow-sm transition-colors hover:bg-[var(--jarvis-primary-hover)]'
        >
          <PlusIcon className='h-5 w-5' />
        </IconButton>
      </div>
    </div>

    {reindexJob && <ReindexBanner job={reindexJob} />}
    {selectionError && (
      <p className='mb-5 text-sm text-[var(--jarvis-warning-text)]'>Default model status is unavailable.</p>
    )}

    <div className='min-h-0 flex-1 overflow-x-auto'>
      <div className='grid min-w-[800px] grid-cols-[22px_minmax(0,1fr)_48px] items-center gap-3 border-b border-[color:var(--jarvis-border)] px-1 py-2.5 text-[13px] text-[var(--jarvis-muted)]'>
        <span className='sr-only'>Default model</span>
        <div className='col-start-2 grid min-w-0 grid-cols-[minmax(300px,420px)_160px_120px_100px] gap-3'>
          <span>Model</span>
          <span>Provider</span>
          <span>Mode</span>
          <span>Last updated</span>
        </div>
        <span className='sr-only'>Actions</span>
      </div>

      {loading && (
        <div className='flex min-h-56 flex-col items-center justify-center gap-3 text-sm text-[var(--jarvis-muted)]'>
          <div className='h-7 w-7 animate-spin rounded-full border-2 border-[color:var(--jarvis-border-strong)] border-b-[var(--jarvis-spinner)]' />
          Loading models...
        </div>
      )}

      {!loading && error && (
        <div className='flex min-h-56 flex-col items-center justify-center px-6 text-center'>
          <p className='text-base font-semibold text-[var(--jarvis-text-strong)]'>Failed to load models</p>
          <p className='mt-2 max-w-lg text-sm text-[var(--jarvis-muted)]'>{error}</p>
          <button type='button' onClick={onRetry} className='btn-primary mt-5'>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && models.length === 0 && (
        <div className='flex min-h-56 flex-col items-center justify-center px-6 text-center'>
          <CpuChipIcon className='h-9 w-9 text-[var(--jarvis-faint)]' />
          <p className='mt-3 text-base font-semibold text-[var(--jarvis-text-strong)]'>No models found</p>
          <p className='mt-2 text-sm text-[var(--jarvis-muted)]'>
            {hasActiveConditions
              ? 'Try adjusting your search or provider filter.'
              : 'Add a model source to make it available through the Jarvis gateway.'}
          </p>
          {!hasActiveConditions && canWrite && (
            <button type='button' onClick={onAdd} className='btn-primary mt-5'>
              Add model
            </button>
          )}
        </div>
      )}

      {!loading &&
        !error &&
        models.map(model => {
          const isDefault =
            selection.defaultWorkflowModelSourceId === model.id || selection.embeddingModelSourceId === model.id;
          const isSetting = settingDefaultId === model.id;
          const isReindexTarget =
            reindexJob?.status === 'running' && reindexJob.targetEmbeddingModelSourceId === model.id;
          const isEmbeddingBlocked = model.mode === 'embedding' && reindexJob?.status === 'running';

          return (
            <div
              key={model.id}
              className='group grid min-w-[800px] grid-cols-[22px_minmax(0,1fr)_48px] items-center gap-3 border-b border-[color:var(--jarvis-border-soft)] px-1 transition hover:bg-[var(--jarvis-card-muted)]'
            >
              <button
                type='button'
                disabled={!canWrite || isDefault || isSetting || isEmbeddingBlocked}
                title={
                  isDefault
                    ? `Default ${model.mode} model`
                    : isReindexTarget
                      ? 'Reindexing with this embedding model'
                      : canWrite
                        ? `Set as default ${model.mode} model`
                        : 'Requires models-write scope'
                }
                onClick={() => onSetDefault(model)}
                className={`flex h-[22px] w-[22px] flex-shrink-0 items-center justify-center rounded-[5px] text-[var(--jarvis-primary-text)] transition hover:bg-[var(--jarvis-primary-soft)] disabled:cursor-default ${
                  isDefault || isSetting || isReindexTarget
                    ? 'opacity-100'
                    : 'opacity-0 group-hover:opacity-100 focus:opacity-100'
                }`}
              >
                {isSetting || isReindexTarget ? (
                  <ArrowPathIcon className='h-3.5 w-3.5 animate-spin' />
                ) : (
                  <StarIcon className='h-3.5 w-3.5' fill={isDefault ? 'currentColor' : 'none'} />
                )}
              </button>
              <button
                type='button'
                onClick={() => onOpenModel(model.id)}
                className='grid min-w-0 grid-cols-[minmax(300px,420px)_160px_120px_100px] items-center gap-3 py-4 text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-[var(--jarvis-primary)]'
              >
                <span className='min-w-0'>
                  <span className='flex min-w-0 items-center gap-2'>
                    <span className='truncate text-[14.5px] font-medium text-[var(--jarvis-text-strong)]'>
                      {model.displayName}
                    </span>
                    <ModelDefaultBadge model={model} selection={selection} />
                  </span>
                  <span className='mt-0.5 block truncate text-xs text-[var(--jarvis-muted)]'>
                    {model.description || model.tags.join(' · ') || 'No description'}
                  </span>
                </span>
                <span className='min-w-0'>
                  <span
                    className={`inline-flex rounded-md px-2 py-1 text-xs font-semibold ${PROVIDER_BADGE_CLASSES[model.providerType]}`}
                  >
                    {PROVIDER_LABELS[model.providerType]}
                  </span>
                </span>
                <span>
                  <span
                    className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${MODE_BADGE_CLASSES[model.mode]}`}
                  >
                    {MODE_LABELS[model.mode]}
                  </span>
                </span>
                <span className='text-[13px] text-[var(--jarvis-muted)]'>
                  {formatTimeSince(model.updatedAt) || '—'}
                </span>
              </button>
              <span className='flex items-center justify-end pr-1'>
                {canWrite && (
                  <IconButton
                    ariaLabel={`Edit ${model.displayName}`}
                    tooltip='Edit'
                    onClick={() => onEdit(model.id)}
                    size='card'
                    className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
                  >
                    <PencilSquareIcon className='h-3.5 w-3.5' />
                  </IconButton>
                )}
              </span>
            </div>
          );
        })}
    </div>

    {!loading && !error && pagination.totalPages > 1 && (
      <div className='flex flex-shrink-0 items-center justify-between border-t border-[color:var(--jarvis-border)] px-1 py-3'>
        <span className='text-xs text-[var(--jarvis-muted)]'>
          Page {pagination.page} of {pagination.totalPages} · {pagination.total} models
        </span>
        <div className='flex gap-2'>
          <button
            type='button'
            onClick={() => onPageChange(pagination.page - 1)}
            disabled={pagination.page <= 1}
            className='inline-flex items-center gap-1 rounded-md border border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] px-2.5 py-1.5 text-xs font-medium text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-50'
          >
            <ChevronLeftIcon className='h-3.5 w-3.5' /> Previous
          </button>
          <button
            type='button'
            onClick={() => onPageChange(pagination.page + 1)}
            disabled={pagination.page >= pagination.totalPages}
            className='inline-flex items-center gap-1 rounded-md border border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] px-2.5 py-1.5 text-xs font-medium text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-50'
          >
            Next <ChevronRightIcon className='h-3.5 w-3.5' />
          </button>
        </div>
      </div>
    )}
  </section>
);

export default ModelListView;
