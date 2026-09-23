import type React from 'react';

import type { SkillSyncJob } from '@/services/skillSyncSource/type';
import UTILS from '@/utils';

import { getSkillErrorLabel } from './skillSyncJobUtils';

interface GithubLastSyncDetailsProps {
  job: SkillSyncJob;
}

const STATUS_LABELS: Record<SkillSyncJob['status'], string> = {
  pending: 'Pending',
  syncing: 'Syncing',
  success: 'Succeeded',
  partial_success: 'Partially succeeded',
  failed: 'Failed',
};

/** Outcome of the last GitHub sync: counts, the job error, and each failed skill or skipped path. */
const GithubLastSyncDetails: React.FC<GithubLastSyncDetailsProps> = ({ job }) => {
  const { applySummary, skillErrors } = job;
  const skippedPaths = job.discoverySummary.skippedPaths ?? [];
  const finishedLabel = UTILS.formatTimeSince(job.finishedAt);

  return (
    <div className='mt-8 border-t border-[color:var(--jarvis-border)] pt-6'>
      <h3 className='mb-1 text-lg font-medium text-[var(--jarvis-text-strong)]'>Last Sync</h3>
      <p className='mb-4 text-sm text-[var(--jarvis-muted)]'>
        {STATUS_LABELS[job.status]}
        {finishedLabel && ` · ${finishedLabel}`} · {applySummary.skillsCreated} created, {applySummary.skillsUpdated}{' '}
        updated, {applySummary.skillsDeleted} deleted, {applySummary.skillsFailed} failed
      </p>

      {job.error && (
        <p className='mb-4 rounded-md bg-[var(--jarvis-danger-soft)] p-3 text-sm text-[var(--jarvis-danger-text)]'>
          {job.errorCode && <span className='mr-2 font-mono text-xs'>{job.errorCode}</span>}
          {job.error}
        </p>
      )}

      {skillErrors.length > 0 && (
        <div className='mb-4'>
          <h4 className='mb-2 text-sm font-medium text-[var(--jarvis-text)]'>Failed skills ({skillErrors.length})</h4>
          <ul className='space-y-2'>
            {skillErrors.map((skillError, index) => (
              <li
                key={`${skillError.path ?? skillError.skillName ?? 'skill'}-${index}`}
                className='rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-3 text-sm'
              >
                <div className='flex flex-wrap items-center gap-2'>
                  <span className='font-medium text-[var(--jarvis-text-strong)]'>{getSkillErrorLabel(skillError)}</span>
                  {skillError.skillName && skillError.path && (
                    <span className='font-mono text-xs text-[var(--jarvis-subtle)]'>{skillError.path}</span>
                  )}
                  <span className='font-mono text-xs text-[var(--jarvis-danger-text)]'>{skillError.errorCode}</span>
                </div>
                <p className='mt-1 text-[var(--jarvis-muted)]'>{skillError.error}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {skippedPaths.length > 0 && (
        <div>
          <h4 className='mb-2 text-sm font-medium text-[var(--jarvis-text)]'>Skipped paths ({skippedPaths.length})</h4>
          <ul className='space-y-1 font-mono text-xs text-[var(--jarvis-muted)]'>
            {skippedPaths.map(path => (
              <li key={path}>{path}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};

export default GithubLastSyncDetails;
