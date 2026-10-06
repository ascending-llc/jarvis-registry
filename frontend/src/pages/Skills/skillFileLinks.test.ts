import { describe, expect, test } from 'vitest';

import { resolveSkillFilePath } from './skillFileLinks';

const FILE_PATHS = ['SKILL.md', 'references/design-system.md', 'scripts/design-system.md'];

describe('resolveSkillFilePath', () => {
  test('matches an absolute gateway URL to a file in the current skill', () => {
    expect(
      resolveSkillFilePath('https://jarvis-demo.ascendingdc.com/gateway/references/design-system.md', FILE_PATHS),
    ).toBe('references/design-system.md');
  });

  test('matches relative skill file links', () => {
    expect(resolveSkillFilePath('./references/design-system.md', FILE_PATHS)).toBe('references/design-system.md');
  });

  test('prefers the longest matching file path', () => {
    expect(resolveSkillFilePath('/gateway/scripts/design-system.md', FILE_PATHS)).toBe('scripts/design-system.md');
  });

  test('does not match links that are not files in the current skill', () => {
    expect(resolveSkillFilePath('https://example.com/docs/design-system.md', FILE_PATHS)).toBeNull();
    expect(resolveSkillFilePath('mailto:team@example.com', FILE_PATHS)).toBeNull();
  });
});
