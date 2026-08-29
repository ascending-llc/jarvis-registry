export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export type SkillPermissions = {
  VIEW: boolean;
  EDIT: boolean;
  DELETE: boolean;
  SHARE: boolean;
};

export type SkillFileMetadata = {
  id: string;
  relativePath: string;
  mimeType: string;
  bytes: number;
  isBinary?: boolean | null;
  isExecutable: boolean;
  source?: string | null;
};

export type SkillMetadata = {
  id: string;
  name: string;
  displayTitle?: string | null;
  description: string;
  category: string;
  tags: string[];
  path: string;
  version: number;
  fileCount: number;
  alwaysApply: boolean;
  enabled: boolean;
  author: string;
  authorName: string;
  source: string;
  sourceMetadata?: { [key: string]: JsonValue } | null;
  createdByRegistry: boolean;
  permissions?: SkillPermissions | null;
  updatedAt?: string | null;
  deletedAt?: string | null;
};

export type SkillDetail = Omit<SkillMetadata, 'path' | 'deletedAt'> & {
  body: string;
  frontmatter: { [key: string]: JsonValue };
  userInvocable: boolean;
  disableModelInvocation: boolean;
  allowedTools?: string[] | null;
  createdAt?: string | null;
  files: SkillFileMetadata[];
};

export type SkillFileContent = {
  relativePath: string;
  content?: string | null;
  body?: string | null;
  mimeType: string;
  isBinary?: boolean | null;
  available: boolean;
  unavailableReason?: string | null;
};

export type SkillListResponse = { skills: SkillMetadata[] };

export type CreateSkillRequest = {
  name: string;
  displayTitle: string;
  description: string;
  body: string;
  category: string;
  tags: string[];
  alwaysApply: boolean;
  userInvocable: boolean;
  disableModelInvocation: boolean;
  allowedTools?: string[] | null;
};

export type UpdateSkillRequest = Partial<CreateSkillRequest>;
export type ToggleSkillRequest = { enabled: boolean };
export type ToggleSkillResponse = { id: string; enabled: boolean };
