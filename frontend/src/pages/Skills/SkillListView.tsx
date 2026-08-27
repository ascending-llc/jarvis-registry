import { Menu, Transition } from '@headlessui/react';
import { ChevronRightIcon, DocumentTextIcon, PencilSquareIcon, PlusIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment } from 'react';

import type { SkillMetadata } from '@/services/skill/type';

import { getSkillDisplayName } from './skillDraft';

type SkillListViewProps = {
  skills: SkillMetadata[];
  loading: boolean;
  error: string | null;
  hasActiveConditions: boolean;
  onRetry: () => void;
  onOpenSkill: (skillId: string) => void;
  onCreate: () => void;
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
  error,
  hasActiveConditions,
  onRetry,
  onOpenSkill,
  onCreate,
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

      <Menu as='div' className='relative flex-shrink-0'>
        <Menu.Button
          aria-label='Add skill'
          className='inline-flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)] transition hover:text-[var(--jarvis-text-strong)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)]'
        >
          <PlusIcon className='h-4 w-4' />
        </Menu.Button>
        <Transition
          as={Fragment}
          enter='transition ease-out duration-100'
          enterFrom='transform opacity-0 scale-95'
          enterTo='transform opacity-100 scale-100'
          leave='transition ease-in duration-75'
          leaveFrom='transform opacity-100 scale-100'
          leaveTo='transform opacity-0 scale-95'
        >
          <Menu.Items className='absolute right-0 z-30 mt-2 w-[234px] origin-top-right rounded-[10px] border border-[color:var(--jarvis-border-strong)] bg-[var(--jarvis-card)] p-1.5 shadow-xl focus:outline-none'>
            <Menu.Item>
              {({ active }) => (
                <button
                  type='button'
                  onClick={onCreate}
                  className={`flex w-full items-center gap-3 rounded-[7px] p-2.5 text-left text-[13.5px] text-[var(--jarvis-text)] transition ${
                    active ? 'bg-[var(--jarvis-card-muted)]' : ''
                  }`}
                >
                  <PencilSquareIcon className='h-[15px] w-[15px] text-[var(--jarvis-muted)]' />
                  Write skill instructions
                </button>
              )}
            </Menu.Item>
          </Menu.Items>
        </Transition>
      </Menu>
    </div>

    <div className='min-h-0 flex-1 overflow-x-auto'>
      <div className='grid min-w-[560px] grid-cols-[2fr_1fr_1fr_30px] gap-3 border-b border-[color:var(--jarvis-border)] px-1 py-2.5 text-[13px] text-[var(--jarvis-muted)]'>
        <span>Skill</span>
        <span>Last updated</span>
        <span>Author</span>
        <span className='sr-only'>Open</span>
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
          <button
            type='button'
            key={skill.id}
            onClick={() => onOpenSkill(skill.id)}
            className='grid min-w-[560px] w-full grid-cols-[2fr_1fr_1fr_30px] items-center gap-3 border-b border-[color:var(--jarvis-border-soft)] px-1 py-4 text-left transition hover:bg-[var(--jarvis-card-muted)] focus:outline-none focus:ring-2 focus:ring-inset focus:ring-[var(--jarvis-primary)]'
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
            <span className='truncate text-[13.5px] text-[var(--jarvis-muted)]'>{skill.authorName || '—'}</span>
            <ChevronRightIcon className='h-3.5 w-3.5 justify-self-end text-[var(--jarvis-faint)]' />
          </button>
        ))}
    </div>
  </section>
);

export default SkillListView;
