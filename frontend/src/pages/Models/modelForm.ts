import type { CreateModelSourceRequest, ModelSourceDetail, UpdateModelSourceRequest } from '@/services/model/type';

import { isBackendSupportedProvider } from './constants';
import type { ModelFormState } from './types';

export const createEmptyModelForm = (): ModelFormState => ({
  providerType: 'aws_bedrock',
  mode: 'chat',
  displayName: '',
  description: '',
  awsRegion: 'us-east-1',
  modelIdOrArn: '',
  baseModelId: '',
  endpoint: '',
  deploymentName: '',
  apiVersion: '2024-10-21',
  apiKey: '',
  baseUrl: '',
});

export const modelDetailToForm = (detail: ModelSourceDetail): ModelFormState => {
  const base = {
    ...createEmptyModelForm(),
    providerType: detail.providerType,
    mode: detail.mode,
    displayName: detail.displayName,
    description: detail.description ?? '',
  };

  if (detail.providerConfig.providerType === 'aws_bedrock') {
    return {
      ...base,
      awsRegion: detail.providerConfig.awsRegion,
      modelIdOrArn: detail.providerConfig.modelIdOrArn,
      baseModelId: detail.providerConfig.baseModelId,
    };
  }

  return {
    ...base,
    endpoint: detail.providerConfig.endpoint,
    deploymentName: detail.providerConfig.deploymentName,
    baseModelId: detail.providerConfig.baseModelId,
    apiVersion: detail.providerConfig.apiVersion,
  };
};

const getProviderConfig = (form: ModelFormState) => {
  if (!isBackendSupportedProvider(form.providerType)) return null;
  if (form.providerType === 'aws_bedrock') {
    return {
      providerType: form.providerType,
      awsRegion: form.awsRegion.trim(),
      modelIdOrArn: form.modelIdOrArn.trim(),
      baseModelId: form.baseModelId.trim(),
    } as const;
  }

  return {
    providerType: form.providerType,
    endpoint: form.endpoint.trim(),
    deploymentName: form.deploymentName.trim(),
    baseModelId: form.baseModelId.trim(),
    apiVersion: form.apiVersion.trim(),
    ...(form.apiKey.trim() ? { apiKey: form.apiKey.trim() } : {}),
  } as const;
};

export const toCreateModelRequest = (form: ModelFormState): CreateModelSourceRequest | null => {
  const providerConfig = getProviderConfig(form);
  if (!providerConfig) return null;
  return {
    displayName: form.displayName.trim(),
    ...(form.description.trim() ? { description: form.description.trim() } : {}),
    mode: form.mode,
    providerConfig,
  };
};

export const toUpdateModelRequest = (form: ModelFormState): UpdateModelSourceRequest | null => {
  const providerConfig = getProviderConfig(form);
  if (!providerConfig) return null;
  return {
    displayName: form.displayName.trim(),
    description: form.description.trim(),
    mode: form.mode,
    providerConfig,
  };
};

export const slugifyModelName = (displayName: string): string =>
  displayName
    .toLocaleLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '') || 'model-name';
