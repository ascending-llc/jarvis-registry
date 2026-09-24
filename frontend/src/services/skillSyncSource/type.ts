import type { AxiosRequestConfig } from 'axios';

export type SkillSyncProviderType = 'github';

export type SkillSyncSourceStatus = 'active' | 'deleting' | 'deleted';

export type SkillSyncStatus = 'idle' | 'pending' | 'syncing' | 'success' | 'partial_success' | 'failed';

export type SkillSyncJobType = 'full_sync' | 'config_resync' | 'delete_sync';

export type SkillSyncTriggerType = 'manual' | 'oauth_callback' | 'api';

export type SkillSyncJobStatus = 'pending' | 'syncing' | 'success' | 'partial_success' | 'failed';

export type SkillSyncJobPhase =
  | 'queued'
  | 'downloading'
  | 'extracting'
  | 'discovering'
  | 'applying'
  | 'completed'
  | 'failed';

export interface SkillSyncSourcePermissions {
  VIEW: boolean;
  EDIT: boolean;
  DELETE: boolean;
  SHARE: boolean;
}

export interface SkillSyncSourceStats {
  skillCount: number;
  fileCount: number;
}

export interface SkillSyncSourceLastSync {
  jobId: string;
  status: SkillSyncJobStatus;
  startedAt: string | null;
  finishedAt: string | null;
  commitSha: string;
}

export interface SkillSyncRequestSnapshot {
  owner?: string;
  repo?: string;
  ref?: string;
  paths?: string[];
  action?: string;
  configRevision: number;
}

export interface SkillSyncDiscoverySummary {
  discoveredSkillCount: number;
  discoveredFileCount: number;
  skippedPaths: string[];
}

export interface SkillSyncApplySummary {
  skillsCreated: number;
  skillsUpdated: number;
  skillsDeleted: number;
  skillsFailed: number;
  filesCreated: number;
  filesUpdated: number;
  filesDeleted: number;
}

export type SkillSyncSkillErrorCode =
  | 'skill_parse_failed'
  | 'skill_name_missing'
  | 'skill_name_mismatch'
  | 'duplicate_skill_name'
  | 'file_too_large'
  | 'too_many_files'
  | 'skill_too_large'
  | 'write_failed'
  | 'delete_failed';

export type SkillSyncSkillErrorPhase = 'extraction' | 'discovery' | 'apply' | 'delete';

export interface SkillSyncSkillError {
  skillPath: string;
  upstreamId: string;
  errorCode: SkillSyncSkillErrorCode;
  errorMessage: string;
  phase: SkillSyncSkillErrorPhase;
}

export interface SkillSyncJob {
  id: string;
  sourceId: string;
  jobType: SkillSyncJobType;
  triggerType: SkillSyncTriggerType;
  status: SkillSyncJobStatus;
  phase: SkillSyncJobPhase;
  requestSnapshot: SkillSyncRequestSnapshot;
  discoverySummary: SkillSyncDiscoverySummary;
  applySummary: SkillSyncApplySummary;
  skillErrors: SkillSyncSkillError[];
  errorCode: string | null;
  error: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface SkillSyncSource {
  id: string;
  providerType: SkillSyncProviderType;
  displayName: string;
  description?: string | null;
  tags: string[];
  owner: string;
  repo: string;
  ref: string;
  paths: string[];
  status: SkillSyncSourceStatus;
  syncStatus: SkillSyncStatus;
  syncMessage?: string | null;
  stats: SkillSyncSourceStats;
  lastSync?: SkillSyncSourceLastSync | null;
  permissions: SkillSyncSourcePermissions;
  createdAt: string;
  updatedAt: string;
}

/** The requesting user's GitHub authorization for one source; tokens are per user, per source. */
export interface SkillSyncSourceAuthorization {
  connected: boolean;
}

export interface SkillSyncSourceDetail extends SkillSyncSource {
  githubAppClientId: string;
  hasClientSecret: boolean;
  authorization: SkillSyncSourceAuthorization;
  recentJobs: SkillSyncJob[];
  createdBy?: string | null;
  updatedBy?: string | null;
}

export interface GetSkillSyncSourcesParams {
  syncStatus?: SkillSyncStatus;
  tag?: string;
  query?: string;
  page?: number;
  perPage?: number;
}

export interface SkillSyncSourcePagination {
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
}

export interface GetSkillSyncSourcesResponse {
  sources: SkillSyncSource[];
  pagination: SkillSyncSourcePagination;
}

export interface CreateSkillSyncSourceRequest {
  displayName: string;
  description?: string;
  tags?: string[];
  owner: string;
  repo: string;
  ref?: string;
  paths: string[];
  githubAppClientId: string;
  githubAppClientSecret: string;
}

export type UpdateSkillSyncSourceRequest = Partial<Omit<CreateSkillSyncSourceRequest, 'description'>> & {
  /** `null` clears the stored description. */
  description?: string | null;
  syncAfterUpdate?: boolean;
};

export interface SkillSyncTriggerResponse {
  job: SkillSyncJob | null;
  needsAuthorization: boolean;
  authorizeUrl: string | null;
}

export type SkillSyncRequest = { dryRun: true } | { dryRun?: false };

export interface SkillSyncTestResponse {
  ok: boolean;
  needsAuthorization: boolean;
  detail: string | null;
}

export type SkillSyncResponse = SkillSyncTriggerResponse | SkillSyncTestResponse;

export interface DeleteSkillSyncSourceResponse {
  sourceId: string;
  jobId: string;
  status: 'deleting';
}

export type GetSkillSyncJobConfig = AxiosRequestConfig;
