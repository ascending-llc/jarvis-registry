import { afterEach, describe, expect, test, vi } from 'vitest';

const config = vi.hoisted(() => ({ basePath: '' }));

vi.mock('@/config', () => ({ getBasePathForUrl: () => config.basePath }));

const { isLoginBrowserPath, isProtectedBrowserPath } = await import('./routes');

afterEach(() => {
  config.basePath = '';
});

describe('isProtectedBrowserPath', () => {
  test.each([
    ['/', true],
    ['/federation-edit', true],
    ['/skill-sync-sources', true],
    ['/skill-sync-sources/', true],
    ['/skill-sync-sources/abc123', true],
    ['/Skill-Sync-Sources/abc123', true],
    ['/skill-sync-sources/abc123/extra', false],
    ['/login', false],
    ['/not-a-route', false],
  ])('%s -> %s without a base path', (pathname, expected) => {
    expect(isProtectedBrowserPath(pathname)).toBe(expected);
  });

  test('matches skill sync detail paths under a base path', () => {
    config.basePath = '/gateway/';
    expect(isProtectedBrowserPath('/gateway/skill-sync-sources/abc123')).toBe(true);
    expect(isProtectedBrowserPath('/gateway')).toBe(true);
    expect(isProtectedBrowserPath('/skill-sync-sources/abc123')).toBe(false);
    expect(isProtectedBrowserPath('/gateway/login')).toBe(false);
  });
});

describe('isLoginBrowserPath', () => {
  test('recognizes the login route with and without a base path', () => {
    expect(isLoginBrowserPath('/login')).toBe(true);
    config.basePath = '/gateway';
    expect(isLoginBrowserPath('/gateway/login/')).toBe(true);
    expect(isLoginBrowserPath('/login')).toBe(false);
  });
});
