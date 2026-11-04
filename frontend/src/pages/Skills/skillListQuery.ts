import type { SkillMetadata } from '@/services/skill/type';

import { getSkillSourceDisplay } from './SkillSourceChip';
import { getSkillDisplayName } from './skillDraft';

export type SkillSortColumn = 'name' | 'source' | 'author' | 'updatedAt';
export type SkillSortDirection = 'asc' | 'desc';
export type SkillSort = { column: SkillSortColumn; direction: SkillSortDirection };

export const DEFAULT_SKILL_SORT: SkillSort = { column: 'updatedAt', direction: 'desc' };

export const SKILL_SORT_FIRST_DIRECTION: Record<SkillSortColumn, SkillSortDirection> = {
  name: 'asc',
  source: 'asc',
  author: 'asc',
  updatedAt: 'desc',
};

export const getNextSkillSort = (current: SkillSort, clicked: SkillSortColumn): SkillSort => ({
  column: clicked,
  direction:
    current.column === clicked
      ? current.direction === 'asc'
        ? 'desc'
        : 'asc'
      : SKILL_SORT_FIRST_DIRECTION[clicked],
});

export const matchesSkillSearch = (skill: SkillMetadata, searchTerm: string): boolean => {
  const normalized = searchTerm.trim().toLocaleLowerCase();
  if (!normalized) return true;

  const fields = [
    getSkillDisplayName(skill),
    skill.name,
    skill.description,
    skill.authorName,
    skill.category,
    ...(skill.tags ?? []),
  ].map(value => (value ?? '').toLocaleLowerCase());

  return normalized.split(/\s+/).every(word => fields.some(field => field.includes(word)));
};

const getUpdatedTimestamp = (updatedAt?: string | null): number | null => {
  if (!updatedAt?.trim()) return null;
  const timestamp = new Date(updatedAt).getTime();
  return Number.isNaN(timestamp) ? null : timestamp;
};

const compareText = (left: string, right: string): number =>
  left.localeCompare(right, undefined, { sensitivity: 'base' });

const compareFallback = (left: SkillMetadata, right: SkillMetadata): number =>
  compareText(getSkillDisplayName(left), getSkillDisplayName(right)) ||
  (left.id < right.id ? -1 : left.id > right.id ? 1 : 0);

const getSortKey = (skill: SkillMetadata, column: SkillSortColumn): string | number | null => {
  switch (column) {
    case 'name':
      return getSkillDisplayName(skill);
    case 'source':
      return getSkillSourceDisplay(skill.origin).label;
    case 'author':
      return skill.authorName?.trim() ? skill.authorName : null;
    case 'updatedAt':
      return getUpdatedTimestamp(skill.updatedAt);
  }
};

export const sortSkills = (skills: SkillMetadata[], sort: SkillSort): SkillMetadata[] => {
  const direction = sort.direction === 'asc' ? 1 : -1;
  return [...skills].sort((left, right) => {
    const leftKey = getSortKey(left, sort.column);
    const rightKey = getSortKey(right, sort.column);
    if (leftKey === null && rightKey === null) return compareFallback(left, right);
    if (leftKey === null) return 1;
    if (rightKey === null) return -1;

    const primary =
      typeof leftKey === 'number' && typeof rightKey === 'number'
        ? leftKey - rightKey
        : compareText(String(leftKey), String(rightKey));
    return primary * direction || compareFallback(left, right);
  });
};
