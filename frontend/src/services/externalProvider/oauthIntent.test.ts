import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { clearGithubOauthIntent, consumeGithubOauthIntent, saveGithubOauthIntent } from './oauthIntent';

const KEY = 'jarvis:github-oauth-intent:source-1';

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

let storage: Storage;

beforeEach(() => {
  storage = makeStorage();
  vi.stubGlobal('window', { sessionStorage: storage });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('GitHub OAuth intent storage', () => {
  test('save then consume returns the intent once', () => {
    saveGithubOauthIntent('source-1', 'sync');

    expect(storage.getItem(KEY)).toBe('sync');
    expect(consumeGithubOauthIntent('source-1')).toBe('sync');
    expect(consumeGithubOauthIntent('source-1')).toBeNull();
  });

  test('intents are scoped per source', () => {
    saveGithubOauthIntent('source-1', 'test');

    expect(consumeGithubOauthIntent('source-2')).toBeNull();
    expect(consumeGithubOauthIntent('source-1')).toBe('test');
  });

  test('an invalid stored value is removed and ignored', () => {
    storage.setItem(KEY, 'delete-everything');

    expect(consumeGithubOauthIntent('source-1')).toBeNull();
    expect(storage.getItem(KEY)).toBeNull();
  });

  test('clear removes a saved intent', () => {
    saveGithubOauthIntent('source-1', 'sync');
    clearGithubOauthIntent('source-1');

    expect(consumeGithubOauthIntent('source-1')).toBeNull();
  });

  test('throwing storage is tolerated everywhere', () => {
    const broken = makeStorage();
    const fail = () => {
      throw new Error('SecurityError');
    };
    broken.setItem = fail;
    broken.getItem = fail;
    broken.removeItem = fail;
    vi.stubGlobal('window', { sessionStorage: broken });

    expect(() => saveGithubOauthIntent('source-1', 'sync')).not.toThrow();
    expect(consumeGithubOauthIntent('source-1')).toBeNull();
    expect(() => clearGithubOauthIntent('source-1')).not.toThrow();
  });

  test('inaccessible sessionStorage is tolerated', () => {
    vi.stubGlobal('window', {
      get sessionStorage(): Storage {
        throw new Error('SecurityError');
      },
    });

    expect(() => saveGithubOauthIntent('source-1', 'sync')).not.toThrow();
    expect(consumeGithubOauthIntent('source-1')).toBeNull();
  });

  test('no window (non-browser) is tolerated', () => {
    vi.stubGlobal('window', undefined);

    expect(() => saveGithubOauthIntent('source-1', 'sync')).not.toThrow();
    expect(consumeGithubOauthIntent('source-1')).toBeNull();
    expect(() => clearGithubOauthIntent('source-1')).not.toThrow();
  });
});
