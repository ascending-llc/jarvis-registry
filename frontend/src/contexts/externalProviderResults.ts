import type { Federation, GetFederationsResponse } from '@/services/federation/type';
import type { GetSkillSyncSourcesResponse, SkillSyncSource } from '@/services/skillSyncSource/type';
import { getErrorMessage } from '@/utils/getErrorMessage';

export interface ExternalProviderResults {
  /** Present only when the AWS/Azure fetch succeeded; otherwise keep the previous list. */
  federations?: Federation[];
  /** Present only when the GitHub fetch succeeded; otherwise keep the previous list. */
  skillSyncSources?: SkillSyncSource[];
  error: string | null;
}

/**
 * Merge the two independent provider fetches: one backend failing must not hide the other's
 * providers, and every failure is reported.
 */
export const collectExternalProviderResults = (
  federationResult: PromiseSettledResult<GetFederationsResponse | undefined>,
  skillSyncSourceResult: PromiseSettledResult<GetSkillSyncSourcesResponse | undefined>,
): ExternalProviderResults => {
  const results: ExternalProviderResults = { error: null };
  const errors: string[] = [];

  if (federationResult.status === 'fulfilled') {
    results.federations = federationResult.value?.federations || [];
  } else {
    errors.push(getErrorMessage(federationResult.reason, 'Failed to fetch AWS and Azure providers'));
  }

  if (skillSyncSourceResult.status === 'fulfilled') {
    results.skillSyncSources = skillSyncSourceResult.value?.sources || [];
  } else {
    errors.push(getErrorMessage(skillSyncSourceResult.reason, 'Failed to fetch GitHub providers'));
  }

  if (errors.length > 0) results.error = errors.join(' ');
  return results;
};
