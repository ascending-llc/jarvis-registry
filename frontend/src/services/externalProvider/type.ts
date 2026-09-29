import type { Federation } from '@/services/federation/type';
import type { SkillSyncSource } from '@/services/skillSyncSource/type';

export type ExternalProviderType = Federation['providerType'] | SkillSyncSource['providerType'];

export type ExternalProviderEntity =
  | { backendKind: 'federation'; data: Federation }
  | { backendKind: 'skill-sync-source'; data: SkillSyncSource };
