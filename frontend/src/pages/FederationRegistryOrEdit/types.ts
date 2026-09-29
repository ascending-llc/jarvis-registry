import type { ExternalProviderType } from '@/services/externalProvider/type';

export interface FederationFormConfig {
  providerType: ExternalProviderType;
  displayName: string;
  description: string;
  region: string;
  assumeRoleArn: string;
  resourceTagsFilter: string; // The comma separated string in the form
  projectEndpoint: string;
  tenantId: string;
  clientId: string;
  clientSecret: string;
  tags: string[];
  owner: string;
  repo: string;
  ref: string;
  paths: string[];
  githubAppClientId: string;
  githubAppClientSecret: string;
}

export type FederationFormStringField = Exclude<keyof FederationFormConfig, 'providerType' | 'tags' | 'paths'>;
