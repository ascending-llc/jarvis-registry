import type { ModelSourceMode, ModelSourceProviderType } from '@/services/model/type';

import type { ModelFormProviderType } from './types';

export const MODEL_PROVIDER_OPTIONS: Array<{
  value: ModelFormProviderType;
  label: string;
  auth: string;
}> = [
  { value: 'aws_bedrock', label: 'AWS Bedrock', auth: 'IAM role' },
  { value: 'azure_openai', label: 'Azure OpenAI', auth: 'Workload Identity or API key' },
  { value: 'openai', label: 'OpenAI', auth: 'API key · backend support pending' },
  { value: 'anthropic', label: 'Anthropic', auth: 'API key · backend support pending' },
];

export const PROVIDER_LABELS: Record<ModelFormProviderType, string> = {
  aws_bedrock: 'AWS Bedrock',
  azure_openai: 'Azure OpenAI',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
};

export const PROVIDER_BADGE_CLASSES: Record<ModelFormProviderType, string> = {
  aws_bedrock: 'bg-[var(--jarvis-orange-soft)] text-[var(--jarvis-orange)]',
  azure_openai: 'bg-[var(--jarvis-blue-soft)] text-[var(--jarvis-blue)]',
  openai: 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)]',
  anthropic: 'bg-[var(--jarvis-warning-soft)] text-[var(--jarvis-warning-text)]',
};

export const MODE_LABELS: Record<ModelSourceMode, string> = {
  chat: 'Chat',
  embedding: 'Embedding',
};

export const MODE_BADGE_CLASSES: Record<ModelSourceMode, string> = {
  chat: 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]',
  embedding: 'bg-[var(--jarvis-teal-soft)] text-[var(--jarvis-teal)]',
};

export const AWS_REGION_OPTIONS = ['us-east-1', 'us-east-2', 'us-west-2', 'eu-central-1', 'ap-northeast-1'];

export const isBackendSupportedProvider = (
  providerType: ModelFormProviderType,
): providerType is ModelSourceProviderType => providerType === 'aws_bedrock' || providerType === 'azure_openai';
