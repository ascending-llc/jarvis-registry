import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import {
  confirmGithubAuthorizationRedirect,
  GITHUB_AUTHORIZATION_PROMPT,
  redirectToGithubAuthorization,
} from './githubAuthorization';
import { consumeGithubOauthIntent } from './oauthIntent';

vi.mock('./sync', () => ({
  getSkillSyncSourceOauthUrl: (sourceId: string) => `/api/v1/skill-sync-sources/${sourceId}/oauth/initiate`,
}));

const makeStorage = (): Storage => {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: key => values.get(key) ?? null,
    key: index => [...values.keys()][index] ?? null,
    removeItem: key => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, value);
    },
  };
};

let assign: ReturnType<typeof vi.fn>;

beforeEach(() => {
  assign = vi.fn();
  vi.stubGlobal('window', { location: { assign }, sessionStorage: makeStorage(), confirm: vi.fn() });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('redirectToGithubAuthorization', () => {
  test('stores the intent and navigates to the OAuth initiate URL', () => {
    redirectToGithubAuthorization('source-1', 'test');

    expect(assign).toHaveBeenCalledWith('/api/v1/skill-sync-sources/source-1/oauth/initiate');
    expect(consumeGithubOauthIntent('source-1')).toBe('test');
  });
});

describe('confirmGithubAuthorizationRedirect', () => {
  test('redirects only after the user confirms', () => {
    const confirm = vi.fn().mockReturnValue(true);

    expect(confirmGithubAuthorizationRedirect('source-1', 'sync', confirm)).toBe(true);
    expect(confirm).toHaveBeenCalledWith(GITHUB_AUTHORIZATION_PROMPT);
    expect(assign).toHaveBeenCalledTimes(1);
    expect(consumeGithubOauthIntent('source-1')).toBe('sync');
  });

  test('stays put and stores nothing when the user declines', () => {
    const confirm = vi.fn().mockReturnValue(false);

    expect(confirmGithubAuthorizationRedirect('source-1', 'sync', confirm)).toBe(false);
    expect(assign).not.toHaveBeenCalled();
    expect(consumeGithubOauthIntent('source-1')).toBeNull();
  });

  test('uses window.confirm by default', () => {
    vi.mocked(window.confirm).mockReturnValue(false);

    confirmGithubAuthorizationRedirect('source-1', 'test');

    expect(window.confirm).toHaveBeenCalledWith(GITHUB_AUTHORIZATION_PROMPT);
  });
});
