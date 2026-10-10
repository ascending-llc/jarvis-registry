import { PencilSquareIcon } from '@heroicons/react/24/outline';
import type React from 'react';

import type { ModelGatewaySelection, ModelSourceDetail } from '@/services/model/type';

import { MODE_BADGE_CLASSES, MODE_LABELS, PROVIDER_BADGE_CLASSES, PROVIDER_LABELS } from './constants';
import ModelBackLink from './ModelBackLink';
import ModelDefaultBadge from './ModelDefaultBadge';
import type { ModelDetailError } from './types';

type ModelDetailViewProps = {
  model: ModelSourceDetail | null;
  loading: boolean;
  error: ModelDetailError | null;
  selection: ModelGatewaySelection;
  selectionError: string | null;
  canWrite: boolean;
  onBack: () => void;
  onEdit: () => void;
  onRetry: () => void;
};

const DATE_FORMATTER = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});

const formatDate = (value: string): string => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : DATE_FORMATTER.format(date);
};

const formatTokenCost = (value: number | null): string => (value === null ? '—' : value.toExponential(2));

const DetailField: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className='min-w-0 border-b border-[color:var(--jarvis-border-soft)] py-3'>
    <div className='text-xs text-[var(--jarvis-muted)]'>{label}</div>
    <div className='mt-1 break-words text-sm text-[var(--jarvis-text)]'>{value}</div>
  </div>
);

const ModelDetailView: React.FC<ModelDetailViewProps> = ({
  model,
  loading,
  error,
  selection,
  selectionError,
  canWrite,
  onBack,
  onEdit,
  onRetry,
}) => {
  if (loading) {
    return (
      <section className='flex min-h-0 flex-1 flex-col px-0 pt-2 md:px-8'>
        <ModelBackLink label='Models' onBack={onBack} />
        <div className='flex flex-1 flex-col items-center justify-center gap-3 text-sm text-[var(--jarvis-muted)]'>
          <div className='h-8 w-8 animate-spin rounded-full border-2 border-[color:var(--jarvis-border-strong)] border-b-[var(--jarvis-spinner)]' />
          Loading model...
        </div>
      </section>
    );
  }

  if (error || !model) {
    const title =
      error?.kind === 'forbidden'
        ? 'You do not have access to this model'
        : error?.kind === 'not-found'
          ? 'Model not found'
          : 'Failed to load model';
    return (
      <section className='flex min-h-0 flex-1 flex-col px-0 pt-2 md:px-8'>
        <ModelBackLink label='Models' onBack={onBack} />
        <div className='flex flex-1 flex-col items-center justify-center px-6 text-center'>
          <p className='text-lg font-semibold text-[var(--jarvis-text-strong)]'>{title}</p>
          <p className='mt-2 max-w-lg text-sm text-[var(--jarvis-muted)]'>{error?.message}</p>
          {error?.kind === 'generic' && (
            <button type='button' onClick={onRetry} className='btn-primary mt-5'>
              Retry
            </button>
          )}
        </div>
      </section>
    );
  }

  const config = model.providerConfig;
  const metadata = model.metadata;

  return (
    <section className='flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden px-0 pt-2 md:px-8'>
      <ModelBackLink label='Models' onBack={onBack} />

      <div className='flex flex-shrink-0 flex-wrap items-start justify-between gap-6'>
        <div className='min-w-0 flex-1 basis-80'>
          <div className='mb-2 text-[12.5px] text-[var(--jarvis-muted)]'>Name</div>
          <h1 className='break-words text-[32px] font-bold leading-[1.15] tracking-[-0.01em] text-[var(--jarvis-text-strong)]'>
            {model.displayName}
          </h1>
          <div className='mt-2.5 flex flex-wrap items-center gap-2'>
            <span
              className={`rounded-md px-2 py-1 text-xs font-semibold ${PROVIDER_BADGE_CLASSES[model.providerType]}`}
            >
              {PROVIDER_LABELS[model.providerType]}
            </span>
            <span className={`rounded px-2 py-0.5 text-xs font-medium ${MODE_BADGE_CLASSES[model.mode]}`}>
              {MODE_LABELS[model.mode]}
            </span>
            <ModelDefaultBadge model={model} selection={selection} />
          </div>
        </div>

        {canWrite && (
          <button
            type='button'
            onClick={onEdit}
            className='inline-flex h-[38px] items-center gap-2 rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-3.5 text-[13px] font-semibold text-[var(--jarvis-text)] transition hover:border-[color:var(--jarvis-border-strong)] hover:bg-[var(--jarvis-card-muted)]'
          >
            <PencilSquareIcon className='h-[15px] w-[15px]' />
            Edit
          </button>
        )}
      </div>

      <div className='mt-5 min-h-0 min-w-0 flex-1 overflow-auto pr-1'>
        {selectionError && (
          <p className='mb-5 text-sm text-[var(--jarvis-warning-text)]'>Default model status is unavailable.</p>
        )}
        <div className='mb-2.5 text-[13px] font-semibold text-[var(--jarvis-text)]'>Description</div>
        <div className='min-h-11 w-full rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-input-bg)] px-3 py-2.5 text-sm text-[var(--jarvis-muted)]'>
          {model.description || 'No description'}
        </div>

        <div className='mt-7'>
          <h2 className='text-[13px] font-semibold text-[var(--jarvis-text)]'>Connection</h2>
          <div className='mt-2 grid gap-x-8 sm:grid-cols-2'>
            {config.providerType === 'aws_bedrock' ? (
              <>
                <DetailField label='AWS region' value={config.awsRegion} />
                <DetailField label='Base model ID' value={config.baseModelId} />
                <DetailField label='Model ID or inference profile ARN' value={config.modelIdOrArn} />
              </>
            ) : (
              <>
                <DetailField label='Endpoint' value={config.endpoint} />
                <DetailField label='Deployment name' value={config.deploymentName} />
                <DetailField label='Base model ID' value={config.baseModelId} />
                <DetailField label='API version' value={config.apiVersion} />
                <DetailField label='API key' value={config.hasApiKey ? 'Configured' : 'Not configured'} />
              </>
            )}
          </div>
        </div>

        {metadata && (
          <div className='mt-7'>
            <h2 className='text-[13px] font-semibold text-[var(--jarvis-text)]'>Model metadata</h2>
            {metadata.unavailableReason && (
              <p className='mt-2 text-sm text-[var(--jarvis-muted)]'>{metadata.unavailableReason}</p>
            )}
            <div className='mt-2 grid gap-x-8 sm:grid-cols-2'>
              <DetailField label='Maximum input tokens' value={metadata.maxInputTokens?.toLocaleString() ?? '—'} />
              <DetailField label='Maximum output tokens' value={metadata.maxOutputTokens?.toLocaleString() ?? '—'} />
              <DetailField label='Input cost per token' value={formatTokenCost(metadata.inputCostPerToken)} />
              <DetailField label='Output cost per token' value={formatTokenCost(metadata.outputCostPerToken)} />
              <DetailField
                label='Prompt caching'
                value={metadata.supportsPromptCaching === null ? '—' : metadata.supportsPromptCaching ? 'Yes' : 'No'}
              />
            </div>
          </div>
        )}

        <div className='mt-7 pb-6'>
          <h2 className='text-[13px] font-semibold text-[var(--jarvis-text)]'>Details</h2>
          <div className='mt-2 grid gap-x-8 sm:grid-cols-2'>
            <DetailField label='Created' value={formatDate(model.createdAt)} />
            <DetailField label='Last updated' value={formatDate(model.updatedAt)} />
            <DetailField label='Model source ID' value={model.id} />
            {model.tags.length > 0 && <DetailField label='Tags' value={model.tags.join(', ')} />}
          </div>
        </div>
      </div>
    </section>
  );
};

export default ModelDetailView;
