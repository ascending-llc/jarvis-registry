import type { ProviderType } from '@/services/federation/type';

export interface FederationFormConfig {
  providerType: ProviderType;
  displayName: string;
  description: string;
  region: string;
  assumeRoleArn: string;
  resourceTagsFilter: string; // The comma separated string in the form
  projectEndpoint: string;
  tenantId: string;
  clientId: string;
  clientSecret: string;
}
