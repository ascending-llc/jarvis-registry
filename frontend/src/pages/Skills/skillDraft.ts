import { parseDocument, stringify } from 'yaml';

import type {
  CreateSkillRequest,
  JsonValue,
  SkillDetail,
  SkillMetadata,
  UpdateSkillRequest,
} from '@/services/skill/type';

import type { DraftValidation, ParsedSkillMarkdown, SkillDraft, SkillMarkdownState } from './types';

export const SKILL_CATEGORIES = [
  { label: 'Ideas', color: '#f5b800' },
  { label: 'Travel', color: '#f5b522' },
  { label: 'Learning', color: '#82b9ff' },
  { label: 'Writing', color: '#b66cff' },
  { label: 'Shopping', color: '#c56bea' },
  { label: 'Code', color: '#ff4550' },
  { label: 'Misc.', color: '#82b9ff' },
  { label: 'Roleplay', color: '#ff7b2f' },
  { label: 'Finance', color: '#ff7b2f' },
] as const;

export type SkillCategory = (typeof SKILL_CATEGORIES)[number]['label'];

export const DEFAULT_SKILL_CATEGORY: SkillCategory = 'Misc.';

export const DEFAULT_SKILL_MARKDOWN = `---
name:
description:
---

# Skill Name

Use this skill to ...

## Core behavior

- **Step one** — describe what happens first.
- **Step two** — describe what happens next.`;

type SkillMarkdownSegments = {
  frontmatterSource: string;
  bodySuffix: string;
  body: string;
  lineEnding: '\n' | '\r\n';
};

const FRONTMATTER_PATTERN = /^---(?:\r?\n)([\s\S]*?)(?:\r?\n)---(?=\r?\n|$)/;

const splitSkillMarkdown = (markdown: string): SkillMarkdownSegments => {
  const match = FRONTMATTER_PATTERN.exec(markdown);
  if (!match) throw new Error('SKILL.md must start with valid YAML frontmatter.');

  const bodySuffix = markdown.slice(match[0].length);
  return {
    frontmatterSource: match[1],
    bodySuffix,
    body: bodySuffix.replace(/^\r?\n/, ''),
    lineEnding: match[0].includes('\r\n') ? '\r\n' : '\n',
  };
};

const toFrontmatter = (value: unknown): { [key: string]: JsonValue } => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('SKILL.md frontmatter must be an object.');
  }
  return value as { [key: string]: JsonValue };
};

const toParsedSkillMarkdown = (frontmatter: { [key: string]: JsonValue }, body: string): ParsedSkillMarkdown => ({
  displayTitle: typeof frontmatter.name === 'string' ? frontmatter.name.trim() : '',
  description: typeof frontmatter.description === 'string' ? frontmatter.description.trim() : '',
  body,
  frontmatter,
});

const getMarkdownErrorMessage = (error: unknown): string =>
  error instanceof Error ? error.message : 'Invalid SKILL.md frontmatter.';

const getFrontmatterBoolean = (frontmatter: { [key: string]: JsonValue }, key: string, fallback: boolean): boolean => {
  const value = frontmatter[key];
  return typeof value === 'boolean' ? value : fallback;
};

const getAllowedTools = (frontmatter: { [key: string]: JsonValue }): string[] | null => {
  const value = frontmatter.allowedTools;
  if (value === null || value === undefined) return null;
  return Array.isArray(value) && value.every((item): item is string => typeof item === 'string') ? [...value] : null;
};

export const isSkillCategory = (value: string): value is SkillCategory =>
  SKILL_CATEGORIES.some(category => category.label === value);

export const normalizeSkillCategory = (value: string): SkillCategory =>
  isSkillCategory(value) ? value : DEFAULT_SKILL_CATEGORY;

export const getSkillDisplayName = (skill: { name: string; displayTitle?: string | null }): string =>
  skill.displayTitle?.trim() || skill.name;

export const formatSkillVersion = (version: number | null): string => `V${version ?? 1}`;

export const slugifySkillName = (displayTitle: string): string =>
  displayTitle
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
    .replace(/-+$/g, '');

export const parseSkillMarkdown = (markdown: string): ParsedSkillMarkdown => {
  const segments = splitSkillMarkdown(markdown);
  const document = parseDocument(segments.frontmatterSource, { keepSourceTokens: true });
  if (document.errors.length > 0) throw new Error(document.errors[0].message);
  return toParsedSkillMarkdown(toFrontmatter(document.toJS()), segments.body);
};

export const createSkillMarkdownState = (markdown: string): SkillMarkdownState => ({
  value: markdown,
  parsed: parseSkillMarkdown(markdown),
  invalidInput: null,
});

export const getSkillMarkdownInput = (state: SkillMarkdownState): string => state.invalidInput?.value ?? state.value;

export const applySkillMarkdownInput = (state: SkillMarkdownState, value: string): SkillMarkdownState => {
  try {
    return createSkillMarkdownState(value);
  } catch (error) {
    return {
      ...state,
      invalidInput: { value, message: getMarkdownErrorMessage(error) },
    };
  }
};

export const composeSkillMarkdown = (detail: SkillDetail): string => {
  const frontmatter = {
    ...detail.frontmatter,
    name: getSkillDisplayName(detail),
    description: detail.description,
    alwaysApply: detail.alwaysApply,
    userInvocable: detail.userInvocable,
    disableModelInvocation: detail.disableModelInvocation,
    allowedTools: detail.allowedTools ?? null,
  };
  const yaml = stringify(frontmatter, { lineWidth: 0 }).trimEnd();
  const body = detail.body ? `\n\n${detail.body.replace(/^\n+/, '')}` : '';
  return `---\n${yaml}\n---${body}`;
};

export const updateSkillMarkdownMetadata = (
  state: SkillMarkdownState,
  updates: { displayTitle?: string; description?: string },
): SkillMarkdownState => {
  if (state.invalidInput) return state;

  const segments = splitSkillMarkdown(state.value);
  const document = parseDocument(segments.frontmatterSource, { keepSourceTokens: true });
  if (document.errors.length > 0) throw new Error(document.errors[0].message);
  toFrontmatter(document.toJS());

  if (updates.displayTitle !== undefined) document.set('name', updates.displayTitle);
  if (updates.description !== undefined) document.set('description', updates.description);

  const frontmatter = toFrontmatter(document.toJS());
  const yaml = document.toString({ lineWidth: 0 }).trimEnd().replace(/\n/g, segments.lineEnding);
  const value = `---${segments.lineEnding}${yaml}${segments.lineEnding}---${segments.bodySuffix}`;
  return {
    value,
    parsed: toParsedSkillMarkdown(frontmatter, segments.body),
    invalidInput: null,
  };
};

export const createDraft = (detail: SkillDetail, markdown = composeSkillMarkdown(detail)): SkillDraft => ({
  id: detail.id,
  stableName: detail.name,
  markdown: createSkillMarkdownState(markdown),
  category: normalizeSkillCategory(detail.category),
  enabled: detail.enabled,
  version: detail.version,
  authorName: detail.authorName,
  files: detail.files.map(file => ({ ...file })),
  permissions: detail.permissions ? { ...detail.permissions } : detail.permissions,
});

export const createEmptyDraft = (authorName: string): SkillDraft => ({
  id: null,
  stableName: '',
  markdown: createSkillMarkdownState(DEFAULT_SKILL_MARKDOWN),
  category: DEFAULT_SKILL_CATEGORY,
  enabled: false,
  version: null,
  authorName,
  files: [],
  permissions: null,
});

export const cloneDraft = (draft: SkillDraft): SkillDraft => ({
  ...draft,
  markdown: {
    value: draft.markdown.value,
    parsed: {
      ...draft.markdown.parsed,
      frontmatter: { ...draft.markdown.parsed.frontmatter },
    },
    invalidInput: draft.markdown.invalidInput ? { ...draft.markdown.invalidInput } : null,
  },
  files: draft.files.map(file => ({ ...file })),
  permissions: draft.permissions ? { ...draft.permissions } : draft.permissions,
});

export const validateDraft = (draft: SkillDraft): DraftValidation => {
  if (draft.markdown.invalidInput) {
    return { valid: false, message: draft.markdown.invalidInput.message };
  }

  const parsed = draft.markdown.parsed;
  if (!parsed.displayTitle) return { valid: false, message: 'Name is required.' };
  if (parsed.displayTitle.length > 128) return { valid: false, message: 'Name must be 128 characters or fewer.' };
  if (!parsed.description) return { valid: false, message: 'Description is required.' };
  if (parsed.description.length > 1024) {
    return { valid: false, message: 'Description must be 1024 characters or fewer.' };
  }
  if (parsed.body.length > 100_000) return { valid: false, message: 'Skill instructions are too long.' };
  if (!isSkillCategory(draft.category)) return { valid: false, message: 'Choose a valid category.' };
  if (draft.id === null && !slugifySkillName(parsed.displayTitle)) {
    return { valid: false, message: 'Name must include letters or numbers that can form a skill identifier.' };
  }

  return { valid: true, parsed };
};

export const toCreateRequest = (draft: SkillDraft): CreateSkillRequest => {
  const parsed = draft.markdown.parsed;
  return {
    name: slugifySkillName(parsed.displayTitle),
    displayTitle: parsed.displayTitle,
    description: parsed.description,
    body: parsed.body,
    category: draft.category,
    tags: [],
    alwaysApply: getFrontmatterBoolean(parsed.frontmatter, 'alwaysApply', false),
    userInvocable: getFrontmatterBoolean(parsed.frontmatter, 'userInvocable', true),
    disableModelInvocation: getFrontmatterBoolean(parsed.frontmatter, 'disableModelInvocation', false),
    allowedTools: getAllowedTools(parsed.frontmatter),
  };
};

export const toUpdateRequest = (draft: SkillDraft): UpdateSkillRequest => {
  const parsed = draft.markdown.parsed;
  return {
    displayTitle: parsed.displayTitle,
    description: parsed.description,
    body: parsed.body,
    category: draft.category,
    alwaysApply: getFrontmatterBoolean(parsed.frontmatter, 'alwaysApply', false),
    userInvocable: getFrontmatterBoolean(parsed.frontmatter, 'userInvocable', true),
    disableModelInvocation: getFrontmatterBoolean(parsed.frontmatter, 'disableModelInvocation', false),
    allowedTools: getAllowedTools(parsed.frontmatter),
  };
};

export const metadataFromDetail = (detail: SkillDetail): SkillMetadata => ({
  id: detail.id,
  name: detail.name,
  displayTitle: detail.displayTitle,
  description: detail.description,
  category: normalizeSkillCategory(detail.category),
  tags: detail.tags,
  path: detail.name,
  version: detail.version,
  fileCount: detail.fileCount,
  alwaysApply: detail.alwaysApply,
  enabled: detail.enabled,
  author: detail.author,
  authorName: detail.authorName,
  source: detail.source,
  sourceMetadata: detail.sourceMetadata,
  createdByRegistry: detail.createdByRegistry,
  permissions: detail.permissions,
  updatedAt: detail.updatedAt,
  deletedAt: null,
});
