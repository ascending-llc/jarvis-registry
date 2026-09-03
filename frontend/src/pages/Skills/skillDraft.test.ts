import { describe, expect, test } from 'vitest';

import type { SkillDetail } from '@/services/skill/type';

import {
  composeSkillMarkdown,
  createDraft,
  createEmptyDraft,
  parseSkillMarkdown,
  toCreateRequest,
  toUpdateRequest,
} from './skillDraft';

const makeDetail = (overrides: Partial<SkillDetail> = {}): SkillDetail => ({
  id: 'skill-1',
  name: 'review-skill',
  displayTitle: 'Review Skill',
  description: 'Review pull requests',
  body: '# Instructions\n\nReview carefully.',
  frontmatter: {},
  userInvocable: true,
  disableModelInvocation: false,
  allowedTools: null,
  category: 'Code',
  tags: [],
  version: 1,
  fileCount: 0,
  alwaysApply: false,
  enabled: true,
  author: 'author-1',
  authorName: 'Test Author',
  source: 'inline',
  createdByRegistry: true,
  files: [],
  ...overrides,
});

describe('composeSkillMarkdown', () => {
  test('renders Claude Code fields with canonical kebab-case keys', () => {
    const markdown = composeSkillMarkdown(
      makeDetail({
        alwaysApply: true,
        frontmatter: {
          allowedTools: ['Read', 'Grep'],
          argumentHint: '[pull-request]',
          disableModelInvocation: true,
          userInvocable: false,
          license: 'MIT',
        },
      }),
    );

    const parsed = parseSkillMarkdown(markdown);
    expect(parsed.frontmatter).toMatchObject({
      'allowed-tools': ['Read', 'Grep'],
      'argument-hint': '[pull-request]',
      'disable-model-invocation': true,
      'user-invocable': false,
      license: 'MIT',
    });
    expect(parsed.frontmatter).not.toHaveProperty('allowedTools');
    expect(parsed.frontmatter).not.toHaveProperty('alwaysApply');
  });

  test('strips Chat-authored inline frontmatter instead of doubling it', () => {
    const markdown = composeSkillMarkdown(
      makeDetail({
        body: `---
name: chat-skill
description: Written by Chat
license: Apache-2.0
foo: inline
---
# Chat body`,
      }),
    );

    const parsed = parseSkillMarkdown(markdown);
    expect(markdown.match(/^---$/gm)).toHaveLength(2);
    expect(parsed.body).toBe('# Chat body');
    expect(parsed.frontmatter.license).toBe('Apache-2.0');
    expect(parsed.frontmatter.foo).toBe('inline');
    expect(parsed.frontmatter).not.toHaveProperty('metadata');
  });

  test('uses stored frontmatter before elevated fields and inline body values', () => {
    const markdown = composeSkillMarkdown(
      makeDetail({
        allowedTools: ['TopLevel'],
        frontmatter: {
          allowedTools: ['Stored'],
          license: 'MIT',
          custom: 'stored',
          metadata: { custom: 'explicit', owner: 'registry' },
          arguments: ['stored-argument'],
          disallowedTools: 'Write',
        },
        body: `---
name: inline
description: Inline
allowed-tools:
  - Inline
license: Apache-2.0
custom: inline
arguments:
  - inline-argument
inline-only: true
metadata:
  inline-only: true
---
Body`,
      }),
    );

    const parsed = parseSkillMarkdown(markdown);
    expect(parsed.frontmatter['allowed-tools']).toEqual(['Stored']);
    expect(parsed.frontmatter['disallowed-tools']).toBe('Write');
    expect(parsed.frontmatter.license).toBe('MIT');
    expect(parsed.frontmatter.custom).toBe('stored');
    expect(parsed.frontmatter.arguments).toEqual(['stored-argument']);
    expect(parsed.frontmatter['inline-only']).toBe(true);
    expect(parsed.frontmatter.metadata).toEqual({ custom: 'explicit', owner: 'registry' });
  });

  test('recomposes an API response without relocating open-ended Claude Code fields', () => {
    const markdown = composeSkillMarkdown(
      makeDetail({
        frontmatter: {
          allowedTools: ['Bash(git add *)', 'Bash(git status *)'],
          arguments: ['subcommand'],
          disallowedTools: 'Write',
          'future-field': { enabled: true },
        },
      }),
    );

    const parsed = parseSkillMarkdown(markdown);
    expect(parsed.frontmatter).toMatchObject({
      'allowed-tools': ['Bash(git add *)', 'Bash(git status *)'],
      arguments: ['subcommand'],
      'disallowed-tools': 'Write',
      'future-field': { enabled: true },
    });
    expect(parsed.frontmatter).not.toHaveProperty('metadata');
  });
});

describe('skill request conversion', () => {
  test('round-trips the complete raw frontmatter and keeps alwaysApply separate', () => {
    const markdown = `---
name: Request Skill
description: Request description
allowed-tools:
  - Read
license: MIT
custom-key: custom-value
---
Body`;
    const draft = createDraft(makeDetail(), markdown);
    draft.alwaysApply = true;

    const createRequest = toCreateRequest(draft);
    const updateRequest = toUpdateRequest(draft);

    expect(createRequest.alwaysApply).toBe(true);
    expect(createRequest.frontmatter).toEqual({
      name: 'Request Skill',
      description: 'Request description',
      'allowed-tools': ['Read'],
      license: 'MIT',
      'custom-key': 'custom-value',
    });
    expect(updateRequest.frontmatter).toEqual(createRequest.frontmatter);
    expect(createRequest).not.toHaveProperty('allowedTools');
    expect(createRequest).not.toHaveProperty('userInvocable');
    expect(createRequest).not.toHaveProperty('disableModelInvocation');
  });

  test('round-trips a pasted skill after a normalized API save and reload', () => {
    const pastedMarkdown = `---
name: round-trip-skill
description: Round-trip description
allowed-tools: Bash(git add *) Bash(git status *)
disallowed-tools: Write
argument-hint: "[pull-request]"
arguments:
  - subcommand
future-field:
  enabled: true
---
# Instructions`;
    const createRequest = toCreateRequest(createDraft(makeDetail(), pastedMarkdown));
    const normalizedAllowedTools = ['Bash(git add *)', 'Bash(git status *)'];
    const reloadedMarkdown = composeSkillMarkdown(
      makeDetail({
        name: createRequest.name,
        displayTitle: createRequest.displayTitle,
        description: createRequest.description,
        body: createRequest.body,
        allowedTools: normalizedAllowedTools,
        frontmatter: {
          allowedTools: normalizedAllowedTools,
          disallowedTools: 'Write',
          argumentHint: '[pull-request]',
          arguments: ['subcommand'],
          'future-field': { enabled: true },
          disableModelInvocation: false,
          userInvocable: true,
        },
      }),
    );

    const reloaded = parseSkillMarkdown(reloadedMarkdown);
    expect(reloaded.body).toBe('# Instructions');
    expect(reloaded.frontmatter).toMatchObject({
      name: 'round-trip-skill',
      description: 'Round-trip description',
      'allowed-tools': normalizedAllowedTools,
      'disallowed-tools': 'Write',
      'argument-hint': '[pull-request]',
      arguments: ['subcommand'],
      'future-field': { enabled: true },
    });
    expect(reloaded.frontmatter).not.toHaveProperty('metadata');
    expect(reloaded.frontmatter).not.toHaveProperty('alwaysApply');
  });

  test('initializes alwaysApply from detail and defaults new drafts to false', () => {
    expect(createDraft(makeDetail({ alwaysApply: true })).alwaysApply).toBe(true);
    expect(createEmptyDraft('Author').alwaysApply).toBe(false);
  });
});
