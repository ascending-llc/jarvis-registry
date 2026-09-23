import { describe, expect, test } from 'vitest';

import type { GetFederationsResponse } from '@/services/federation/type';
import type { GetSkillSyncSourcesResponse } from '@/services/skillSyncSource/type';

import { collectExternalProviderResults } from './externalProviderResults';

const federations = { federations: [{ id: 'fed-1' }] } as GetFederationsResponse;
const sources = { sources: [{ id: 'src-1' }] } as GetSkillSyncSourcesResponse;

const fulfilled = <T>(value: T): PromiseFulfilledResult<T> => ({ status: 'fulfilled', value });
const rejected = (reason: unknown): PromiseRejectedResult => ({ status: 'rejected', reason });

describe('collectExternalProviderResults', () => {
  test('returns both lists when both fetches succeed', () => {
    expect(collectExternalProviderResults(fulfilled(federations), fulfilled(sources))).toEqual({
      federations: federations.federations,
      skillSyncSources: sources.sources,
      error: null,
    });
  });

  test('keeps GitHub providers when the federation fetch fails', () => {
    const results = collectExternalProviderResults(rejected({ detail: 'AWS down' }), fulfilled(sources));
    expect(results.federations).toBeUndefined();
    expect(results.skillSyncSources).toEqual(sources.sources);
    expect(results.error).toBe('AWS down');
  });

  test('keeps AWS/Azure providers when the GitHub fetch fails, with a fallback message', () => {
    const results = collectExternalProviderResults(fulfilled(federations), rejected(null));
    expect(results.federations).toEqual(federations.federations);
    expect(results.skillSyncSources).toBeUndefined();
    expect(results.error).toBe('Failed to fetch GitHub providers');
  });

  test('reports both failures', () => {
    const results = collectExternalProviderResults(
      rejected({ detail: { message: 'federation boom' } }),
      rejected(new Error('github boom')),
    );
    expect(results.error).toBe('federation boom github boom');
  });

  test('treats an empty response as an empty list', () => {
    expect(collectExternalProviderResults(fulfilled(undefined), fulfilled(undefined))).toEqual({
      federations: [],
      skillSyncSources: [],
      error: null,
    });
  });
});
