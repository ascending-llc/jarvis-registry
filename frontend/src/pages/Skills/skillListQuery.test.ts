import { describe, expect, test } from 'vitest';

import type { SkillMetadata, SkillOrigin } from '@/services/skill/type';

import {
  DEFAULT_SKILL_SORT,
  getNextSkillSort,
  matchesSkillSearch,
  type SkillSort,
  type SkillSortColumn,
  sortSkills,
} from './skillListQuery';

const makeSkill = (overrides: Partial<SkillMetadata> & { id: string }): SkillMetadata => ({
  name: overrides.id,
  displayTitle: null,
  description: '',
  category: 'Other',
  tags: [],
  version: 1,
  fileCount: 1,
  alwaysApply: false,
  enabled: true,
  author: 'user-1',
  authorName: 'Author',
  source: 'registry',
  createdByRegistry: true,
  origin: { kind: 'registry' },
  updatedAt: '2026-01-01T00:00:00Z',
  ...overrides,
});

const ids = (skills: SkillMetadata[]): string[] => skills.map(skill => skill.id);

const externalOrigin = (type: string): SkillOrigin => ({
  kind: 'external_provider',
  provider: { id: 'p1', name: 'Provider', type },
});

describe('getNextSkillSort', () => {
  test('defaults to last updated, newest first', () => {
    expect(DEFAULT_SKILL_SORT).toEqual({ column: 'updatedAt', direction: 'desc' });
  });

  test('clicking the active column flips its direction', () => {
    expect(getNextSkillSort({ column: 'author', direction: 'asc' }, 'author')).toEqual({
      column: 'author',
      direction: 'desc',
    });
    expect(getNextSkillSort({ column: 'updatedAt', direction: 'desc' }, 'updatedAt')).toEqual({
      column: 'updatedAt',
      direction: 'asc',
    });
  });

  test.each<SkillSortColumn>(['name', 'source', 'author'])('switching to %s starts ascending', column => {
    expect(getNextSkillSort({ column: 'updatedAt', direction: 'asc' }, column)).toEqual({ column, direction: 'asc' });
  });

  test('switching to updatedAt starts descending', () => {
    expect(getNextSkillSort({ column: 'name', direction: 'desc' }, 'updatedAt')).toEqual({
      column: 'updatedAt',
      direction: 'desc',
    });
  });
});

describe('sortSkills', () => {
  test('default sort orders by last updated, newest first', () => {
    const skills = [
      makeSkill({ id: 'old', updatedAt: '2026-01-01T00:00:00Z' }),
      makeSkill({ id: 'new', updatedAt: '2026-03-01T00:00:00Z' }),
      makeSkill({ id: 'mid', updatedAt: '2026-02-01T00:00:00Z' }),
    ];
    expect(ids(sortSkills(skills, DEFAULT_SKILL_SORT))).toEqual(['new', 'mid', 'old']);
    expect(ids(sortSkills(skills, { column: 'updatedAt', direction: 'asc' }))).toEqual(['old', 'mid', 'new']);
  });

  test('name compares the display name case-insensitively', () => {
    const skills = [
      makeSkill({ id: 'b', name: 'beta' }),
      makeSkill({ id: 'g', name: 'gamma', displayTitle: 'Gamma' }),
      makeSkill({ id: 'a', name: 'zzz', displayTitle: 'alpha' }),
      makeSkill({ id: 'B', name: 'Beta-2', displayTitle: 'Beta' }),
    ];
    expect(ids(sortSkills(skills, { column: 'name', direction: 'asc' }))).toEqual(['a', 'B', 'b', 'g']);
    expect(ids(sortSkills(skills, { column: 'name', direction: 'desc' }))).toEqual(['g', 'B', 'b', 'a']);
  });

  test('source follows the label order Chat < External < GitHub < Registry', () => {
    const skills = [
      makeSkill({ id: 'registry', origin: { kind: 'registry' } }),
      makeSkill({ id: 'github', origin: externalOrigin('github') }),
      makeSkill({ id: 'chat', origin: { kind: 'chat' } }),
      makeSkill({ id: 'external', origin: externalOrigin('gitlab') }),
    ];
    expect(ids(sortSkills(skills, { column: 'source', direction: 'asc' }))).toEqual([
      'chat',
      'external',
      'github',
      'registry',
    ]);
    expect(ids(sortSkills(skills, { column: 'source', direction: 'desc' }))).toEqual([
      'registry',
      'github',
      'external',
      'chat',
    ]);
  });

  test('author compares the trimmed name case-insensitively', () => {
    const skills = [
      makeSkill({ id: 'zed', authorName: ' Zed' }),
      makeSkill({ id: 'adam', authorName: 'adam' }),
      makeSkill({ id: 'mia', authorName: 'Mia' }),
    ];
    expect(ids(sortSkills(skills, { column: 'author', direction: 'asc' }))).toEqual(['adam', 'mia', 'zed']);
    expect(ids(sortSkills(skills, { column: 'author', direction: 'desc' }))).toEqual(['zed', 'mia', 'adam']);
  });

  test.each<SkillSort['direction']>(['asc', 'desc'])('blank authors sort last when %s', direction => {
    const skills = [
      makeSkill({ id: 'blank', name: 'a-blank', authorName: '' }),
      makeSkill({ id: 'bob', authorName: 'Bob' }),
      makeSkill({ id: 'spaces', name: 'b-spaces', authorName: '   ' }),
      makeSkill({ id: 'amy', authorName: 'Amy' }),
    ];
    const sorted = ids(sortSkills(skills, { column: 'author', direction }));
    expect(sorted.slice(2)).toEqual(['blank', 'spaces']);
    expect(sorted.slice(0, 2)).toEqual(direction === 'asc' ? ['amy', 'bob'] : ['bob', 'amy']);
  });

  test.each<SkillSort['direction']>(['asc', 'desc'])('missing or invalid updatedAt sorts last when %s', direction => {
    const skills = [
      makeSkill({ id: 'missing', name: 'a-missing', updatedAt: null }),
      makeSkill({ id: 'old', updatedAt: '2026-01-01T00:00:00Z' }),
      makeSkill({ id: 'invalid', name: 'b-invalid', updatedAt: 'not-a-date' }),
      makeSkill({ id: 'new', updatedAt: '2026-03-01T00:00:00Z' }),
    ];
    const sorted = ids(sortSkills(skills, { column: 'updatedAt', direction }));
    expect(sorted.slice(2)).toEqual(['missing', 'invalid']);
    expect(sorted.slice(0, 2)).toEqual(direction === 'asc' ? ['old', 'new'] : ['new', 'old']);
  });

  test.each<SkillSort['direction']>(['asc', 'desc'])('equal keys fall back to name then id when %s', direction => {
    const skills = [
      makeSkill({ id: 'id-2', name: 'same' }),
      makeSkill({ id: 'id-3', name: 'Alpha' }),
      makeSkill({ id: 'id-1', name: 'same' }),
    ];
    expect(ids(sortSkills(skills, { column: 'source', direction }))).toEqual(['id-3', 'id-1', 'id-2']);
  });

  test('does not mutate the input array', () => {
    const skills = [
      makeSkill({ id: 'a', updatedAt: '2026-01-01T00:00:00Z' }),
      makeSkill({ id: 'b', updatedAt: '2026-02-01T00:00:00Z' }),
    ];
    const snapshot = [...skills];
    const sorted = sortSkills(skills, DEFAULT_SKILL_SORT);
    expect(sorted).not.toBe(skills);
    expect(skills).toEqual(snapshot);
    expect(ids(sorted)).toEqual(['b', 'a']);
  });
});

describe('matchesSkillSearch', () => {
  const skill = makeSkill({
    id: 's1',
    name: 'pdf-tools',
    displayTitle: 'Document Helper',
    description: 'Extract text from files',
    authorName: 'Jane Doe',
    category: 'Productivity',
    tags: ['parsing', 'Office'],
  });

  test.each(['', '   ', '\t\n'])('empty or whitespace-only term %j matches everything', term => {
    expect(matchesSkillSearch(skill, term)).toBe(true);
  });

  test.each([
    ['display name', 'helper'],
    ['slug name when displayTitle differs', 'pdf-tools'],
    ['description', 'extract'],
    ['author name', 'jane'],
    ['category', 'productiv'],
    ['tag', 'office'],
  ])('matches on %s', (_field, term) => {
    expect(matchesSkillSearch(skill, term)).toBe(true);
  });

  test('every word may match a different field', () => {
    expect(matchesSkillSearch(skill, 'pdf extract')).toBe(true);
    expect(matchesSkillSearch(skill, '  parsing   jane  ')).toBe(true);
  });

  test('does not match when one word is absent from every field', () => {
    expect(matchesSkillSearch(skill, 'pdf excel')).toBe(false);
  });

  test('matching is case-insensitive', () => {
    expect(matchesSkillSearch(skill, 'PDF EXTRACT')).toBe(true);
    expect(matchesSkillSearch(skill, 'dOcUmEnT')).toBe(true);
  });

  test('null or missing description, tags and other fields do not throw', () => {
    const sparse = {
      ...makeSkill({ id: 'sparse', name: 'sparse-skill' }),
      description: null,
      tags: null,
      category: undefined,
      authorName: null,
    } as unknown as SkillMetadata;
    expect(() => matchesSkillSearch(sparse, 'anything')).not.toThrow();
    expect(matchesSkillSearch(sparse, 'anything')).toBe(false);
    expect(matchesSkillSearch(sparse, 'sparse')).toBe(true);
  });
});
