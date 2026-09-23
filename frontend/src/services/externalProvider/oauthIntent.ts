export type GithubOauthIntent = 'test' | 'sync';

const GITHUB_OAUTH_INTENT_PREFIX = 'jarvis:github-oauth-intent:';
const VALID_GITHUB_OAUTH_INTENTS = new Set<GithubOauthIntent>(['test', 'sync']);

const getStorage = (): Storage | null => {
  if (typeof window === 'undefined') return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};

const getIntentKey = (sourceId: string): string => `${GITHUB_OAUTH_INTENT_PREFIX}${sourceId}`;

export const saveGithubOauthIntent = (sourceId: string, intent: GithubOauthIntent): void => {
  try {
    getStorage()?.setItem(getIntentKey(sourceId), intent);
  } catch {
    // OAuth can still proceed when browser storage is unavailable.
  }
};

export const consumeGithubOauthIntent = (sourceId: string): GithubOauthIntent | null => {
  const storage = getStorage();
  if (!storage) return null;

  try {
    const key = getIntentKey(sourceId);
    const value = storage.getItem(key);
    storage.removeItem(key);
    return value && VALID_GITHUB_OAUTH_INTENTS.has(value as GithubOauthIntent) ? (value as GithubOauthIntent) : null;
  } catch {
    return null;
  }
};

export const clearGithubOauthIntent = (sourceId: string): void => {
  try {
    getStorage()?.removeItem(getIntentKey(sourceId));
  } catch {
    // Nothing else is required when browser storage is unavailable.
  }
};
