import type { SkillSyncJob, SkillSyncSkillError } from '@/services/skillSyncSource/type';

const FINISHED_JOB_STATUSES = new Set<SkillSyncJob['status']>(['success', 'partial_success', 'failed']);

/** The most recent finished sync (not delete) job; `recentJobs` is newest first. */
export const getLatestFinishedSyncJob = (recentJobs: SkillSyncJob[]): SkillSyncJob | null =>
  recentJobs.find(job => job.jobType !== 'delete_sync' && FINISHED_JOB_STATUSES.has(job.status)) ?? null;

export const getSkillErrorLabel = (skillError: SkillSyncSkillError): string =>
  skillError.skillName || skillError.path || 'Unknown skill';
