import { type GithubOauthIntent, saveGithubOauthIntent } from './oauthIntent';
import { getSkillSyncSourceOauthUrl } from './sync';

export const GITHUB_AUTHORIZATION_PROMPT =
  'Jarvis needs your GitHub authorization for this source. Continue to GitHub?';

/** Leave Jarvis for GitHub's OAuth screen; the intent says what to resume on return. */
export const redirectToGithubAuthorization = (sourceId: string, intent: GithubOauthIntent): void => {
  saveGithubOauthIntent(sourceId, intent);
  window.location.assign(getSkillSyncSourceOauthUrl(sourceId));
};

/**
 * Ask before an implicit redirect (a sync or test that came back `needsAuthorization`).
 * Returns whether the redirect started.
 */
export const confirmGithubAuthorizationRedirect = (
  sourceId: string,
  intent: GithubOauthIntent,
  confirm: (message: string) => boolean = message => window.confirm(message),
): boolean => {
  if (!confirm(GITHUB_AUTHORIZATION_PROMPT)) return false;
  redirectToGithubAuthorization(sourceId, intent);
  return true;
};
