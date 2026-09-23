import { describe, expect, test } from 'vitest';

import type { SkillSyncJob } from '@/services/skillSyncSource/type';

import { getLatestFinishedSyncJob, getSkillErrorLabel } from './skillSyncJobUtils';

const makeJob = (id: string, overrides: Partial<SkillSyncJob> = {}): SkillSyncJob => ({
  id,
  sourceId: 'source-1',
  jobType: 'full_sync',
  triggerType: 'manual',
  status: 'success',
  phase: 'completed',
  requestSnapshot: { configRevision: 1 },
  discoverySummary: { discoveredSkillCount: 0, discoveredFileCount: 0, skippedPaths: [] },
  applySummary: {
    skillsCreated: 0,
    skillsUpdated: 0,
    skillsDeleted: 0,
    skillsFailed: 0,
    filesCreated: 0,
    filesUpdated: 0,
    filesDeleted: 0,
  },
  skillErrors: [],
  errorCode: null,
  error: null,
  startedAt: null,
  finishedAt: null,
  createdAt: '2026-09-01T00:00:00Z',
  updatedAt: '2026-09-01T00:00:00Z',
  ...overrides,
});

describe('getLatestFinishedSyncJob', () => {
  test('skips running jobs and returns the newest finished one', () => {
    const jobs = [
      makeJob('running', { status: 'syncing' }),
      makeJob('queued', { status: 'pending' }),
      makeJob('partial', { status: 'partial_success' }),
      makeJob('older', { status: 'success' }),
    ];
    expect(getLatestFinishedSyncJob(jobs)?.id).toBe('partial');
  });

  test('returns failed jobs too', () => {
    expect(getLatestFinishedSyncJob([makeJob('failed', { status: 'failed' })])?.id).toBe('failed');
  });

  test('ignores delete jobs', () => {
    const jobs = [makeJob('delete', { jobType: 'delete_sync', status: 'failed' }), makeJob('sync')];
    expect(getLatestFinishedSyncJob(jobs)?.id).toBe('sync');
  });

  test('returns null when nothing has finished', () => {
    expect(getLatestFinishedSyncJob([])).toBeNull();
    expect(getLatestFinishedSyncJob([makeJob('running', { status: 'syncing' })])).toBeNull();
  });
});

describe('getSkillErrorLabel', () => {
  test('prefers the skill name, then the path', () => {
    expect(getSkillErrorLabel({ skillName: 'review', path: 'skills/review', errorCode: 'E', error: 'x' })).toBe(
      'review',
    );
    expect(getSkillErrorLabel({ skillName: null, path: 'skills/review', errorCode: 'E', error: 'x' })).toBe(
      'skills/review',
    );
    expect(getSkillErrorLabel({ errorCode: 'E', error: 'x' })).toBe('Unknown skill');
  });
});
