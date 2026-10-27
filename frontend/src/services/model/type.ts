export type ModelSourceMode = 'chat' | 'embedding';

export type ModelSourceProviderType = 'aws_bedrock' | 'azure_openai' | 'anthropic' | 'openai';

export type AwsBedrockModelConfig = {
  providerType: 'aws_bedrock';
  awsRegion: string;
  modelIdOrArn: string;
  baseModelId: string;
};

export type AzureOpenAIModelConfig = {
  providerType: 'azure_openai';
  endpoint: string;
  deploymentName: string;
  baseModelId: string;
  apiVersion: string;
  hasApiKey: boolean;
};

export type AzureOpenAIModelConfigInput = Omit<AzureOpenAIModelConfig, 'hasApiKey'> & {
  apiKey?: string;
};

export type FirstPartyModelConfig = {
  providerType: 'anthropic' | 'openai';
  modelId: string;
  baseUrl: string | null;
  hasApiKey: boolean;
};

export type FirstPartyModelConfigInput = Omit<FirstPartyModelConfig, 'hasApiKey' | 'baseUrl'> & {
  baseUrl?: string;
  apiKey?: string;
};

export type ModelSourceProviderConfig = AwsBedrockModelConfig | AzureOpenAIModelConfig | FirstPartyModelConfig;

export type ModelSourceProviderConfigInput =
  | AwsBedrockModelConfig
  | AzureOpenAIModelConfigInput
  | FirstPartyModelConfigInput;

export type ModelSourceListItem = {
  id: string;
  displayName: string;
  description: string | null;
  tags: string[];
  mode: ModelSourceMode;
  providerType: ModelSourceProviderType;
  createdAt: string;
  updatedAt: string;
};

export type ModelSourceMetadata = {
  maxInputTokens: number | null;
  maxOutputTokens: number | null;
  inputCostPerToken: number | null;
  outputCostPerToken: number | null;
  supportsPromptCaching: boolean | null;
  unavailableReason: string | null;
};

export type ModelSourceDetail = ModelSourceListItem & {
  providerConfig: ModelSourceProviderConfig;
  metadata?: ModelSourceMetadata | null;
  createdBy: string | null;
  updatedBy: string | null;
};

export type ModelSourceListRequest = {
  mode?: ModelSourceMode;
  providerType?: ModelSourceProviderType;
  tag?: string;
  query?: string;
  page?: number;
  per_page?: number;
};

export type ModelSourcePagination = {
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
};

export type ModelSourceListResponse = {
  modelSources: ModelSourceListItem[];
  pagination: ModelSourcePagination;
};

export type CreateModelSourceRequest = {
  displayName: string;
  description?: string;
  tags?: string[];
  mode: ModelSourceMode;
  providerConfig: ModelSourceProviderConfigInput;
};

export type UpdateModelSourceRequest = Partial<CreateModelSourceRequest>;

export type DeleteModelSourceResponse = {
  id: string;
  deletedAt: string;
};

export type ModelGatewaySelection = {
  defaultWorkflowModelSourceId: string | null;
  embeddingModelSourceId: string | null;
};

export type SetModelGatewaySelectionRequest = {
  modelSourceId: string;
};

export type EmbeddingReindexJobStatus = 'running' | 'completed' | 'failed';

export type EmbeddingReindexJob = {
  id: string;
  status: EmbeddingReindexJobStatus;
  targetEmbeddingModelSourceId: string;
  previousEmbeddingModelSourceId: string | null;
  previousCollectionGeneration: string | null;
  requestedBy: string | null;
  startedAt: string;
  switchedAt: string | null;
  finishedAt: string | null;
  attempts: number;
  error: string | null;
  lastError: string | null;
  leaseOwner: string | null;
  leaseExpiresAt: string | null;
  heartbeatAt: string | null;
};

export type EmbeddingReindexJobsResponse = {
  jobs: EmbeddingReindexJob[];
};
