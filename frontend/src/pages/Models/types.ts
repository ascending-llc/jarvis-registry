import type { ModelSourceMode } from '@/services/model/type';

export type ModelFormProviderType = 'aws_bedrock' | 'azure_openai' | 'openai' | 'anthropic';

export type ModelFormState = {
  providerType: ModelFormProviderType;
  mode: ModelSourceMode;
  displayName: string;
  description: string;
  awsRegion: string;
  modelIdOrArn: string;
  baseModelId: string;
  endpoint: string;
  deploymentName: string;
  apiVersion: string;
  apiKey: string;
  baseUrl: string;
};

export type ModelFormErrors = Partial<Record<keyof ModelFormState, string>>;

export type ModelDetailError = {
  kind: 'forbidden' | 'not-found' | 'generic';
  message: string;
};
