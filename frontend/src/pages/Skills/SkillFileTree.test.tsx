// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import type { SkillFileMetadata } from '@/services/skill/type';

import { SKILL_MARKDOWN_PATH } from './constants';
import SkillFileTree from './SkillFileTree';

const FILES: SkillFileMetadata[] = [
  {
    id: 'design-system',
    relativePath: 'references/design-system.md',
    mimeType: 'text/markdown',
    bytes: 100,
    isExecutable: false,
  },
];

let scrollIntoView: ReturnType<typeof vi.fn>;

beforeEach(() => {
  scrollIntoView = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: scrollIntoView,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SkillFileTree selection visibility', () => {
  test('expands ancestor folders when a nested file is selected externally', () => {
    const { rerender } = render(<SkillFileTree files={FILES} selectedPath={SKILL_MARKDOWN_PATH} onSelect={vi.fn()} />);

    expect(screen.queryByRole('button', { name: 'design-system.md' })).toBeNull();

    rerender(<SkillFileTree files={FILES} selectedPath='references/design-system.md' onSelect={vi.fn()} />);

    expect(screen.getByRole('button', { name: 'references' }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('button', { name: 'design-system.md' }).getAttribute('aria-current')).toBe('page');
  });

  test('scrolls the externally selected file into view', () => {
    const { rerender } = render(<SkillFileTree files={FILES} selectedPath={SKILL_MARKDOWN_PATH} onSelect={vi.fn()} />);
    scrollIntoView.mockClear();
    rerender(<SkillFileTree files={FILES} selectedPath='references/design-system.md' onSelect={vi.fn()} />);

    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
  });
});
