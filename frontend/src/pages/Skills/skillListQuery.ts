import type { SkillMetadata } from '@/services/skill/type';
import type { SkillScanFilter } from '@/types/layout';

import { getSkillSourceDisplay } from './SkillSourceChip';
import { getSkillDisplayName } from './skillDraft';
import { getHighestRisk, isScanActive, SCAN_SEVERITIES } from './skillScan';

export type SkillSortColumn = 'name' | 'source' | 'security' | 'author' | 'updatedAt';
export type SkillSortDirection = 'asc' | 'desc';
export type SkillSort = { column: SkillSortColumn; direction: SkillSortDirection };

export const DEFAULT_SKILL_SORT: SkillSort = { column: 'updatedAt', direction: 'desc' };

export const SKILL_SCAN_FILTERS: { value: SkillScanFilter; label: string; dotClass: string }[] = [
  { value: 'all', label: 'All scans', dotClass: 'bg-[var(--jarvis-primary)]' },
  { value: 'review', label: 'Critical / High', dotClass: 'bg-[var(--jarvis-orange)]' },
  { value: 'findings', label: 'Has findings', dotClass: 'bg-[var(--jarvis-warning)]' },
  { value: 'clean', label: 'No findings', dotClass: 'bg-[var(--jarvis-muted)]' },
  { value: 'outdated', label: 'Out of date', dotClass: 'bg-[var(--jarvis-warning)]' },
  { value: 'unscanned', label: 'Not scanned / in progress', dotClass: 'bg-[var(--jarvis-subtle)]' },
  { value: 'failed', label: 'Failed / can’t scan', dotClass: 'bg-[var(--jarvis-danger)]' },
];

export const matchesSkillScanFilter = (skill: SkillMetadata, filter: SkillScanFilter): boolean => {
  if (filter === 'all') return true;
  const scan = skill.scanSummary;
  if (!scan) return false;
  const completed = scan.scanStatus === 'completed';
  const hasRiskReport = completed && (scan.status === 'succeeded' || scan.status === 'partial');
  const highest = getHighestRisk(scan.severityCounts);
  switch (filter) {
    case 'review':
      return hasRiskReport && (highest === 'critical' || highest === 'high');
    case 'findings':
      return hasRiskReport && highest !== undefined;
    case 'clean':
      return completed && scan.status === 'succeeded' && highest === undefined;
    case 'outdated':
      return scan.stale || scan.outdatedTool;
    case 'unscanned':
      return scan.scanStatus === 'not_scanned' || isScanActive(scan.scanStatus);
    case 'failed':
      return scan.scanStatus === 'failed' || (completed && (scan.status === 'failed' || scan.status === 'skipped'));
  }
};

export const SKILL_SORT_FIRST_DIRECTION: Record<SkillSortColumn, SkillSortDirection> = {
  name: 'asc',
  source: 'asc',
  security: 'asc',
  author: 'asc',
  updatedAt: 'desc',
};

export const getNextSkillSort = (current: SkillSort, clicked: SkillSortColumn): SkillSort =>
  clicked === 'security' && current.column === 'security'
    ? DEFAULT_SKILL_SORT
    : {
        column: clicked,
        direction:
          current.column === clicked
            ? current.direction === 'asc'
              ? 'desc'
              : 'asc'
            : SKILL_SORT_FIRST_DIRECTION[clicked],
      };

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
    case 'security': {
      const scan = skill.scanSummary;
      if (!scan) return null;
      if (scan.scanStatus === 'failed') return 5;
      if (scan.scanStatus === 'pending' || scan.scanStatus === 'scanning') return 8;
      if (scan.scanStatus === 'not_scanned') return 9;
      if (scan.scanStatus !== 'completed') return null;
      if (scan.status === 'failed') return 5;
      if (scan.status === 'skipped') return 6;
      if (scan.status !== 'succeeded' && scan.status !== 'partial') return null;
      const highest = getHighestRisk(scan.severityCounts);
      return highest ? SCAN_SEVERITIES.indexOf(highest) : scan.status === 'partial' ? 6 : 7;
    }
    case 'author':
      return skill.authorName?.trim() || null;
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
