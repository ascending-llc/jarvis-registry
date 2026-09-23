import { describe, expect, test } from 'vitest';

import type { SkillSyncSourceDetail } from '@/services/skillSyncSource/type';

import {
  buildGithubCreatePayload,
  buildGithubUpdatePayload,
  type GithubFormInput,
  hasGithubFormChanges,
  isSafeRepositoryPath,
  normalizePaths,
  normalizeTags,
  validateGithubForm,
} from './formUtils';

const makeSource = (overrides: Partial<SkillSyncSourceDetail> = {}): SkillSyncSourceDetail => ({
  id: 'source-1',
  providerType: 'github',
  displayName: 'Skills',
  description: 'Team skills',
  tags: ['prod'],
  owner: 'octocat',
  repo: 'skills',
  ref: 'main',
  paths: ['skills'],
  status: 'active',
  syncStatus: 'success',
  syncMessage: null,
  stats: { skillCount: 1, fileCount: 2 },
  lastSync: null,
  permissions: { VIEW: true, EDIT: true, DELETE: true, SHARE: true },
  createdAt: '2026-09-01T00:00:00Z',
  updatedAt: '2026-09-01T00:00:00Z',
  githubAppClientId: 'Iv1.client',
  hasClientSecret: true,
  authorization: { connected: true },
  recentJobs: [],
  ...overrides,
});

/** The form exactly as getGithubFormData would load it from makeSource(). */
const makeForm = (overrides: Partial<GithubFormInput> = {}): GithubFormInput => ({
  displayName: 'Skills',
  description: 'Team skills',
  tags: ['prod'],
  owner: 'octocat',
  repo: 'skills',
  ref: 'main',
  paths: ['skills'],
  githubAppClientId: 'Iv1.client',
  githubAppClientSecret: '',
  ...overrides,
});

describe('normalizePaths', () => {
  test('trims, strips trailing slashes, drops blanks and duplicates', () => {
    expect(normalizePaths([' skills/ ', 'skills', '', '  ', 'prompts//', 'prompts'])).toEqual(['skills', 'prompts']);
  });

  test('keeps "." and turns "./" into "."', () => {
    expect(normalizePaths(['.'])).toEqual(['.']);
    expect(normalizePaths(['./'])).toEqual(['.']);
  });

  test('keeps an all-slash path as typed instead of turning it into the repository root', () => {
    expect(normalizePaths(['/'])).toEqual(['/']);
    expect(normalizePaths(['///'])).toEqual(['///']);
  });
});

describe('normalizeTags', () => {
  test('trims, drops blanks, and de-duplicates case-insensitively keeping the first spelling', () => {
    expect(normalizeTags([' Prod ', 'prod', '', 'PROD', 'team'])).toEqual(['Prod', 'team']);
  });
});

describe('isSafeRepositoryPath', () => {
  test.each([
    ['skills', true],
    ['skills/nested', true],
    ['.', true],
    ['/skills', false],
    ['/', false],
    ['skills\\nested', false],
    ['../outside', false],
    ['skills/../outside', false],
  ])('%s -> %s', (path, expected) => {
    expect(isSafeRepositoryPath(path)).toBe(expected);
  });
});

describe('validateGithubForm', () => {
  test('accepts a valid form', () => {
    expect(validateGithubForm(makeForm({ githubAppClientSecret: 'secret' }), { requireSecret: true })).toEqual({});
  });

  test('requires display name, owner, repo, paths and client id', () => {
    const errors = validateGithubForm(
      makeForm({ displayName: ' ', owner: '', repo: '', paths: [''], githubAppClientId: ' ' }),
      { requireSecret: false },
    );
    expect(Object.keys(errors).sort()).toEqual(['displayName', 'githubAppClientId', 'owner', 'paths', 'repo']);
  });

  test('rejects a display name over 128 characters', () => {
    expect(validateGithubForm(makeForm({ displayName: 'x'.repeat(129) }), { requireSecret: false }).displayName).toBe(
      'Display Name must be 128 characters or fewer',
    );
  });

  test('rejects invalid owner and repo names', () => {
    const errors = validateGithubForm(makeForm({ owner: '-bad-', repo: 'bad repo' }), { requireSecret: false });
    expect(errors.owner).toBeDefined();
    expect(errors.repo).toBeDefined();
  });

  test('ref is optional', () => {
    expect(validateGithubForm(makeForm({ ref: '   ' }), { requireSecret: false }).ref).toBeUndefined();
  });

  test.each(['feature..x', 'a//b', 'main@{1}', 'a\\b', '/main', 'r'.repeat(256)])('rejects unsafe ref %s', ref => {
    expect(validateGithubForm(makeForm({ ref }), { requireSecret: false }).ref).toBe(
      'Enter a safe branch, tag, or commit SHA',
    );
  });

  test('accepts branch names with slashes', () => {
    expect(validateGithubForm(makeForm({ ref: 'release/2026.09' }), { requireSecret: false }).ref).toBeUndefined();
  });

  test('rejects a "/" path rather than silently syncing the repository root', () => {
    expect(validateGithubForm(makeForm({ paths: ['/'] }), { requireSecret: false }).paths).toBe(
      'Paths must be safe repository-relative POSIX paths',
    );
  });

  test('rejects parent-directory paths', () => {
    expect(validateGithubForm(makeForm({ paths: ['skills/../../etc'] }), { requireSecret: false }).paths).toBeDefined();
  });

  test('requires the secret only when asked to', () => {
    expect(validateGithubForm(makeForm(), { requireSecret: true }).githubAppClientSecret).toBe(
      'GitHub App Client Secret is required',
    );
    expect(validateGithubForm(makeForm(), { requireSecret: false }).githubAppClientSecret).toBeUndefined();
  });
});

describe('buildGithubCreatePayload', () => {
  test('trims and normalizes every field', () => {
    expect(
      buildGithubCreatePayload(
        makeForm({
          displayName: ' Skills ',
          description: ' desc ',
          tags: ['a', 'A'],
          owner: ' octocat ',
          repo: ' skills ',
          ref: ' dev ',
          paths: ['skills/', 'skills'],
          githubAppClientId: ' id ',
          githubAppClientSecret: ' secret ',
        }),
      ),
    ).toEqual({
      displayName: 'Skills',
      description: 'desc',
      tags: ['a'],
      owner: 'octocat',
      repo: 'skills',
      ref: 'dev',
      paths: ['skills'],
      githubAppClientId: 'id',
      githubAppClientSecret: 'secret',
    });
  });

  test('omits a blank ref so the backend default applies, and a blank description', () => {
    const payload = buildGithubCreatePayload(makeForm({ ref: ' ', description: ' ', githubAppClientSecret: 's' }));
    expect(payload).not.toHaveProperty('ref');
    expect(payload).not.toHaveProperty('description');
  });
});

describe('buildGithubUpdatePayload', () => {
  test('is empty when nothing changed (no credentials resent)', () => {
    expect(buildGithubUpdatePayload(makeSource(), makeForm())).toEqual({});
    expect(hasGithubFormChanges(makeSource(), makeForm())).toBe(false);
  });

  test('ignores whitespace and normalization-only differences', () => {
    const form = makeForm({ displayName: ' Skills ', tags: ['prod', 'PROD'], paths: ['skills/', ' skills '] });
    expect(buildGithubUpdatePayload(makeSource(), form)).toEqual({});
  });

  test('sends only a changed display name', () => {
    expect(buildGithubUpdatePayload(makeSource(), makeForm({ displayName: 'Renamed' }))).toEqual({
      displayName: 'Renamed',
    });
  });

  test('sends each changed field on its own', () => {
    const source = makeSource();
    expect(buildGithubUpdatePayload(source, makeForm({ owner: 'other' }))).toEqual({ owner: 'other' });
    expect(buildGithubUpdatePayload(source, makeForm({ repo: 'other' }))).toEqual({ repo: 'other' });
    expect(buildGithubUpdatePayload(source, makeForm({ ref: 'dev' }))).toEqual({ ref: 'dev' });
    expect(buildGithubUpdatePayload(source, makeForm({ paths: ['skills', 'more'] }))).toEqual({
      paths: ['skills', 'more'],
    });
    expect(buildGithubUpdatePayload(source, makeForm({ tags: [] }))).toEqual({ tags: [] });
    expect(buildGithubUpdatePayload(source, makeForm({ githubAppClientId: 'Iv1.other' }))).toEqual({
      githubAppClientId: 'Iv1.other',
    });
  });

  test('treats a reordered path list as a change', () => {
    const source = makeSource({ paths: ['a', 'b'] });
    expect(buildGithubUpdatePayload(source, makeForm({ paths: ['b', 'a'] }))).toEqual({ paths: ['b', 'a'] });
  });

  test('sends a typed secret, and never sends a blank one', () => {
    expect(buildGithubUpdatePayload(makeSource(), makeForm({ githubAppClientSecret: ' new ' }))).toEqual({
      githubAppClientSecret: 'new',
    });
    expect(buildGithubUpdatePayload(makeSource(), makeForm({ githubAppClientSecret: '   ' }))).toEqual({});
  });

  test('sends description: null when the user clears an existing description', () => {
    expect(buildGithubUpdatePayload(makeSource(), makeForm({ description: '  ' }))).toEqual({ description: null });
  });

  test('sends nothing for a blank description when none is stored', () => {
    expect(buildGithubUpdatePayload(makeSource({ description: null }), makeForm({ description: '' }))).toEqual({});
    expect(buildGithubUpdatePayload(makeSource({ description: undefined }), makeForm({ description: '' }))).toEqual({});
  });

  test('sends a new description trimmed', () => {
    expect(buildGithubUpdatePayload(makeSource(), makeForm({ description: ' New ' }))).toEqual({ description: 'New' });
  });

  test('a blank ref means main: nothing is sent when main is stored', () => {
    expect(buildGithubUpdatePayload(makeSource({ ref: 'main' }), makeForm({ ref: ' ' }))).toEqual({});
  });

  test('a blank ref resets a non-default stored ref to main', () => {
    expect(buildGithubUpdatePayload(makeSource({ ref: 'dev' }), makeForm({ ref: '' }))).toEqual({ ref: 'main' });
  });
});
