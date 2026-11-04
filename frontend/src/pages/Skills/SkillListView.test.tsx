// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import SkillListView from './SkillListView';
import type { SkillSort, SkillSortColumn } from './skillListQuery';

afterEach(cleanup);

const renderList = (sort: SkillSort, onSortChange = vi.fn()) => {
  render(
    <SkillListView
      skills={[]}
      sort={sort}
      onSortChange={onSortChange}
      loading={false}
      refreshing={false}
      error={null}
      hasActiveConditions={false}
      onRetry={vi.fn()}
      onRefresh={vi.fn()}
      onOpenSkill={vi.fn()}
      onCreate={vi.fn()}
      onShare={vi.fn()}
      onDelete={vi.fn()}
    />,
  );
  return onSortChange;
};

const getHeader = (label: string) => screen.getByRole('button', { name: new RegExp(`^(Sort by )?${label}\\b`) });

describe('SkillListView sort headers', () => {
  test.each<[string, SkillSortColumn]>([
    ['Skill', 'name'],
    ['Source', 'source'],
    ['Author', 'author'],
    ['Last updated', 'updatedAt'],
  ])('clicking %s calls onSortChange with %s', (label, column) => {
    const onSortChange = renderList({ column: 'updatedAt', direction: 'desc' });
    fireEvent.click(getHeader(label));
    expect(onSortChange).toHaveBeenCalledTimes(1);
    expect(onSortChange).toHaveBeenCalledWith(column);
  });

  test('only the active column renders a direction icon', () => {
    renderList({ column: 'author', direction: 'asc' });
    expect(screen.getAllByTestId(/^sort-direction-icon-/)).toHaveLength(1);
    expect(getHeader('Author').querySelector('[data-testid="sort-direction-icon-asc"]')).not.toBeNull();
    for (const label of ['Skill', 'Source', 'Last updated']) {
      expect(getHeader(label).querySelector('[data-testid^="sort-direction-icon-"]')).toBeNull();
    }
  });

  test('the direction icon follows the sort direction', () => {
    renderList({ column: 'updatedAt', direction: 'desc' });
    expect(getHeader('Last updated').querySelector('[data-testid="sort-direction-icon-desc"]')).not.toBeNull();
    expect(screen.queryByTestId('sort-direction-icon-asc')).toBeNull();
  });

  test('the active header aria-label reflects the current direction', () => {
    renderList({ column: 'author', direction: 'asc' });
    expect(getHeader('Author').getAttribute('aria-label')).toBe('Author, sorted ascending. Click to sort descending.');
    expect(getHeader('Skill').getAttribute('aria-label')).toBe('Sort by Skill');
    cleanup();

    renderList({ column: 'author', direction: 'desc' });
    expect(getHeader('Author').getAttribute('aria-label')).toBe('Author, sorted descending. Click to sort ascending.');
  });
});
