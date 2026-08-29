import {
  ArrowPathIcon,
  DocumentTextIcon,
  PlusIcon,
  ShareIcon,
  TrashIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';

import IconButton from '@/components/IconButton';
import type { SkillMetadata } from '@/services/skill/type';

import { getSkillDisplayName } from './skillDraft';

type SkillListViewProps = {
  skills: SkillMetadata[];
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  hasActiveConditions: boolean;
  onRetry: () => void;
  onRefresh: () => void;
  onOpenSkill: (skillId: string) => void;
  onCreate: () => void;
  onShare: (skill: SkillMetadata) => void;
  onDelete: (skill: SkillMetadata) => void;
};

const UPDATED_DATE_FORMATTER = new Intl.DateTimeFormat('en-US', {
  month: 'numeric',
  day: 'numeric',
  year: '2-digit',
});

const formatUpdatedDate = (updatedAt?: string | null): string => {
  if (!updatedAt) return '—';
  const date = new Date(updatedAt);
  return Number.isNaN(date.getTime()) ? '—' : UPDATED_DATE_FORMATTER.format(date);
};

const SkillListView: React.FC<SkillListViewProps> = ({
  skills,
  loading,
  refreshing,
  error,
  hasActiveConditions,
  onRetry,
  onRefresh,
  onOpenSkill,
  onCreate,
  onShare,
  onDelete,
}) => (
  <section className='flex min-h-0 flex-1 flex-col overflow-y-auto px-0 pt-2 md:px-8'>
    <div className='mb-2 flex flex-shrink-0 items-start justify-between gap-4'>
      <div className='min-w-0 flex-1'>
        <h1 className='text-2xl font-bold tracking-[-0.01em] text-[var(--jarvis-text-strong)]'>Skills</h1>
        <p className='mb-7 mt-2 text-sm leading-[1.6] text-[var(--jarvis-muted)]'>
          Reusable capabilities agents can invoke — parsing, formatting, redaction, and routing logic packaged for reuse
          across workflows.
        </p>
      </div>

      <div className='flex flex-shrink-0 items-center gap-3'>
        <IconButton
          ariaLabel='Refresh skills'
          tooltip='Refresh'
          onClick={onRefresh}
          disabled={refreshing}
          spinning={refreshing}
          className='flex h-10 w-10 items-center justify-center rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-surface)] text-[var(--jarvis-text)] transition-colors hover:bg-[var(--jarvis-card-muted)]'
        >
          <ArrowPathIcon className='h-4 w-4' />
        </IconButton>
        <IconButton
          ariaLabel='Add skill'
          tooltip='Add skill'
          onClick={onCreate}
          variant='solid'
          className='flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--jarvis-primary)] text-white shadow-sm transition-colors hover:bg-[var(--jarvis-primary-hover)]'
        >
          <PlusIcon className='h-5 w-5' />
        </IconButton>
      </div>
    </div>

    <div className='min-h-0 flex-1 overflow-x-auto'>
      <div className='grid min-w-[640px] grid-cols-[minmax(0,1fr)_64px] items-center gap-3 border-b border-[color:var(--jarvis-border)] px-1 py-2.5 text-[13px] text-[var(--jarvis-muted)]'>
        <div className='grid min-w-0 grid-cols-[2fr_1fr_1fr] gap-3'>
          <span>Skill</span>
          <span>Last updated</span>
          <span>Author</span>
        </div>
        <span className='sr-only'>Actions</span>
      </div>

      {loading && (
        <div className='flex min-h-56 flex-col items-center justify-center gap-3 text-sm text-[var(--jarvis-muted)]'>
          <div className='h-7 w-7 animate-spin rounded-full border-2 border-[color:var(--jarvis-border-strong)] border-b-[var(--jarvis-spinner)]' />
          Loading skills...
        </div>
      )}

      {!loading && error && (
        <div className='flex min-h-56 flex-col items-center justify-center px-6 text-center'>
          <p className='text-base font-semibold text-[var(--jarvis-text-strong)]'>Failed to load skills</p>
          <p className='mt-2 max-w-lg text-sm text-[var(--jarvis-muted)]'>{error}</p>
          <button type='button' onClick={onRetry} className='btn-primary mt-5'>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && skills.length === 0 && (
        <div className='flex min-h-56 flex-col items-center justify-center px-6 text-center'>
          <DocumentTextIcon className='h-9 w-9 text-[var(--jarvis-faint)]' />
          <p className='mt-3 text-base font-semibold text-[var(--jarvis-text-strong)]'>No skills found</p>
          <p className='mt-2 text-sm text-[var(--jarvis-muted)]'>
            {hasActiveConditions
              ? 'Try adjusting your search or status filter.'
              : 'Create a reusable skill by writing its instructions.'}
          </p>
          {!hasActiveConditions && (
            <button type='button' onClick={onCreate} className='btn-primary mt-5'>
              Write skill instructions
            </button>
          )}
        </div>
      )}

      {!loading &&
        !error &&
        skills.map(skill => (
          <div
            key={skill.id}
            className='group grid min-w-[640px] grid-cols-[minmax(0,1fr)_64px] items-center gap-3 border-b border-[color:var(--jarvis-border-soft)] px-1 transition hover:bg-[var(--jarvis-card-muted)]'
          >
            <button
              type='button'
              onClick={() => onOpenSkill(skill.id)}
              className='grid min-w-0 grid-cols-[2fr_1fr_1fr] items-center gap-3 py-4 text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-[var(--jarvis-primary)]'
            >
              <span className='flex min-w-0 items-center gap-2.5'>
                <span className='flex h-[22px] w-[22px] flex-shrink-0 items-center justify-center rounded-[5px] bg-[var(--jarvis-card-muted)] text-[var(--jarvis-muted)]'>
                  <DocumentTextIcon className='h-3 w-3' />
                </span>
                <span className='min-w-0 truncate text-[14.5px] font-medium text-[var(--jarvis-text-strong)]'>
                  {getSkillDisplayName(skill)}
                </span>
                <span
                  className={`ml-1 inline-flex flex-shrink-0 rounded-full px-2 py-[3px] text-[10.5px] font-semibold leading-none ${
                    skill.enabled
                      ? 'bg-[var(--jarvis-success-soft)] text-[var(--jarvis-success-text)]'
                      : 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-muted)]'
                  }`}
                >
                  {skill.enabled ? 'Enabled' : 'Disabled'}
                </span>
              </span>
              <span className='text-[13.5px] text-[var(--jarvis-muted)]'>{formatUpdatedDate(skill.updatedAt)}</span>
              <span className='truncate text-[13.5px] text-[var(--jarvis-muted)]'>
                {skill.authorName || '—'}
              </span>
            </button>
            <span className='flex items-center justify-end gap-0.5 pr-1'>
              {skill.permissions?.SHARE === true && (
                <IconButton
                  ariaLabel={`Share ${getSkillDisplayName(skill)}`}
                  tooltip='Share'
                  onClick={() => onShare(skill)}
                  size='card'
                  className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
                >
                  <ShareIcon className='h-3.5 w-3.5' />
                </IconButton>
              )}
              {skill.permissions?.DELETE === true && (
                <IconButton
                  ariaLabel={`Delete ${getSkillDisplayName(skill)}`}
                  tooltip='Delete'
                  onClick={() => onDelete(skill)}
                  size='card'
                  className='text-[var(--jarvis-danger-text)] hover:bg-[var(--jarvis-danger-soft)] hover:text-[var(--jarvis-danger)]'
                >
                  <TrashIcon className='h-3.5 w-3.5' />
                </IconButton>
              )}
            </span>
          </div>
        ))}
    </div>
  </section>
);

export default SkillListView;
