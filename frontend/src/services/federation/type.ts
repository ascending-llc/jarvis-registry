export type ProviderType = 'aws_agentcore' | 'azure_ai_foundry';

export type FederationStatus = 'active' | 'deleting' | 'deleted';

export type SyncStatus = 'idle' | 'pending' | 'syncing' | 'success' | 'failed';

export type JobType = 'full_sync' | 'config_resync' | 'force_sync' | 'delete_sync';

export type JobStatus = 'pending' | 'syncing' | 'success' | 'failed';

export interface ProviderConfig {
  region?: string;
  assumeRoleArn?: string;
  tenantId?: string;
  subscriptionId?: string;
  resourceGroup?: string;
  resourceTagsFilter?: Record<string, string>;
}

export interface FederationStats {
  mcpServerCount: number;
  agentCount: number;
  toolCount: number;
  importedTotal: number;
}

export interface SyncSummary {
  discoveredMcpServers: number;
  discoveredAgents: number;
  createdMcpServers: number;
  updatedMcpServers: number;
  deletedMcpServers: number;
  unchangedMcpServers: number;
  createdAgents: number;
  updatedAgents: number;
  deletedAgents: number;
  unchangedAgents: number;
  errors: number;
}

export interface FederationJob {
  jobId: string;
  jobType: JobType;
  status: JobStatus;
  startedAt: string;
  finishedAt: string;
  summary: SyncSummary;
}

export interface Federation {
  id: string;
  providerType: ProviderType;
  displayName: string;
  description?: string | null;
  tags?: string[];
  status: FederationStatus;
  syncStatus: SyncStatus;
  syncMessage?: string | null;
  providerConfig?: ProviderConfig;
  stats: FederationStats;
  lastSync?: FederationJob | null;
  recentJobs?: FederationJob[];
  version: number;
  createdBy: string;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface GetFederationsParams {
  providerType?: ProviderType;
  syncStatus?: SyncStatus;
  tag?: string;
  tags?: string[];
  query?: string;
  page?: number;
  per_page?: number;
}

export interface Pagination {
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
}

export interface GetFederationsResponse {
  federations: Federation[];
  pagination: Pagination;
}

export interface CreateFederationRequest {
  providerType: ProviderType;
  displayName: string;
  description?: string;
  tags?: string[];
  providerConfig?: ProviderConfig;
}

export interface UpdateFederationRequest {
  displayName: string;
  description?: string | null;
  tags?: string[];
  providerConfig?: ProviderConfig;
  version: number;
  syncAfterUpdate?: boolean;
}
