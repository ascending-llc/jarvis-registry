import type { JsonValue, SkillDetail, SkillFileMetadata } from '@/services/skill/type';
import type { ResourceStatusFilter } from '@/types/layout';

export type SkillStatusFilter = ResourceStatusFilter;
export type EditorMode = 'preview' | 'edit';

export type ParsedSkillMarkdown = {
  displayTitle: string;
  description: string;
  body: string;
  frontmatter: { [key: string]: JsonValue };
};

export type SkillMarkdownState = {
  value: string;
  parsed: ParsedSkillMarkdown;
  invalidInput: { value: string; message: string } | null;
};

export type SkillDraft = {
  id: string | null;
  stableName: string;
  markdown: SkillMarkdownState;
  category: string;
  alwaysApply: boolean;
  enabled: boolean;
  version: number | null;
  authorName: string;
  files: SkillFileMetadata[];
  permissions: SkillDetail['permissions'];
};

export type DraftValidation = { valid: true; parsed: ParsedSkillMarkdown } | { valid: false; message: string };

export type SkillPageError = {
  kind: 'forbidden' | 'not-found' | 'generic';
  message: string;
};
