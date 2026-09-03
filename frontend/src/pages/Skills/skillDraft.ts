import { Document, isMap, isScalar, isSeq, parseDocument } from 'yaml';

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

const CLAUDE_CODE_FRONTMATTER_KEBAB_KEYS: Record<string, string> = {
  allowedTools: 'allowed-tools',
  disallowedTools: 'disallowed-tools',
  argumentHint: 'argument-hint',
  disableModelInvocation: 'disable-model-invocation',
  userInvocable: 'user-invocable',
};

const CLAUDE_CODE_FRONTMATTER_CAMEL_KEYS = Object.fromEntries(
  Object.entries(CLAUDE_CODE_FRONTMATTER_KEBAB_KEYS).map(([camelKey, kebabKey]) => [kebabKey, camelKey]),
);

// Claude Code's own docs accept allowed-tools as a space-/comma-separated string, and the backend's
// `_tokenize_allowed_tools` already round-trips a single-space join back into the original paren-aware
// entries — so the editor renders it as a string rather than a YAML list, matching Claude Code's own style.
const ALLOWED_TOOLS_KEBAB_KEY = CLAUDE_CODE_FRONTMATTER_KEBAB_KEYS.allowedTools;

const REGISTRY_BOOKKEEPING_FRONTMATTER_KEYS = new Set([
  'name',
  'description',
  'displayTitle',
  'category',
  'alwaysApply',
  'tags',
]);

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

export const splitSkillMarkdown = (markdown: string): SkillMarkdownSegments => {
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

const normalizeFrontmatterKeys = (frontmatter: {
  [key: string]: JsonValue | undefined;
}): { [key: string]: JsonValue } => {
  const normalized: { [key: string]: JsonValue } = {};
  for (const [key, value] of Object.entries(frontmatter)) {
    if (value === undefined) continue;
    normalized[CLAUDE_CODE_FRONTMATTER_CAMEL_KEYS[key] ?? key] = value;
  }
  return normalized;
};

const firstDefined = (...values: (JsonValue | undefined)[]): JsonValue | undefined =>
  values.find(value => value !== undefined);

const parseInlineBodyFrontmatter = (body: string): { frontmatter: { [key: string]: JsonValue }; body: string } => {
  try {
    const parsed = parseSkillMarkdown(body);
    return { frontmatter: parsed.frontmatter, body: parsed.body };
  } catch {
    return { frontmatter: {}, body };
  }
};

const toKebabCaseFrontmatter = (frontmatter: { [key: string]: JsonValue }): { [key: string]: JsonValue } =>
  Object.fromEntries(
    Object.entries(frontmatter).map(([key, value]) => [CLAUDE_CODE_FRONTMATTER_KEBAB_KEYS[key] ?? key, value]),
  );

// Renders `allowed-tools` as a plain space-joined string instead of a YAML list (Claude Code's own
// preferred style for this field) and every other array-valued field in flow style (`[a, b]`) instead of
// YAML's default block list style, without changing the parsed value of either.
const stringifyFrontmatterYaml = (kebabFrontmatter: { [key: string]: JsonValue }): string => {
  const document = new Document(kebabFrontmatter);
  if (isMap(document.contents)) {
    for (const item of document.contents.items) {
      const key = isScalar(item.key) ? String(item.key.value) : String(item.key);
      const value = kebabFrontmatter[key];
      if (key === ALLOWED_TOOLS_KEBAB_KEY && Array.isArray(value)) {
        item.value = document.createNode(value.map(entry => String(entry)).join(' '));
        continue;
      }
      if (isSeq(item.value)) item.value.flow = true;
    }
  }
  return document.toString({ lineWidth: 0, flowCollectionPadding: false }).trimEnd();
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
  const inlineBody = parseInlineBodyFrontmatter(detail.body);
  const inlineFrontmatter = normalizeFrontmatterKeys(inlineBody.frontmatter);
  const storedFrontmatter = normalizeFrontmatterKeys(detail.frontmatter);
  const frontmatter: { [key: string]: JsonValue } = {
    ...inlineFrontmatter,
    ...storedFrontmatter,
  };

  for (const key of REGISTRY_BOOKKEEPING_FRONTMATTER_KEYS) delete frontmatter[key];
  frontmatter.name = getSkillDisplayName(detail);
  frontmatter.description = detail.description;
  frontmatter.allowedTools =
    firstDefined(storedFrontmatter.allowedTools, detail.allowedTools, inlineFrontmatter.allowedTools) ?? null;
  frontmatter.disableModelInvocation =
    firstDefined(
      storedFrontmatter.disableModelInvocation,
      detail.disableModelInvocation,
      inlineFrontmatter.disableModelInvocation,
    ) ?? false;
  frontmatter.userInvocable =
    firstDefined(storedFrontmatter.userInvocable, detail.userInvocable, inlineFrontmatter.userInvocable) ?? true;

  const yaml = stringifyFrontmatterYaml(toKebabCaseFrontmatter(frontmatter));
  const body = inlineBody.body ? `\n${inlineBody.body.replace(/^\n+/, '')}` : '';
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
  const yaml = document
    .toString({ lineWidth: 0, flowCollectionPadding: false })
    .trimEnd()
    .replace(/\n/g, segments.lineEnding);
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
  alwaysApply: detail.alwaysApply,
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
  alwaysApply: false,
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
    alwaysApply: draft.alwaysApply,
    frontmatter: parsed.frontmatter,
  };
};

export const toUpdateRequest = (draft: SkillDraft): UpdateSkillRequest => {
  const parsed = draft.markdown.parsed;
  return {
    displayTitle: parsed.displayTitle,
    description: parsed.description,
    body: parsed.body,
    category: draft.category,
    alwaysApply: draft.alwaysApply,
    frontmatter: parsed.frontmatter,
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
