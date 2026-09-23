import type {
  CreateSkillSyncSourceRequest,
  SkillSyncSourceDetail,
  UpdateSkillSyncSourceRequest,
} from '@/services/skillSyncSource/type';

/** The backend applies this ref when a create request omits it. */
export const DEFAULT_GITHUB_REF = 'main';

const GITHUB_OWNER_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/;
const GITHUB_REPO_PATTERN = /^[A-Za-z0-9._-]+$/;
const MAX_DISPLAY_NAME_LENGTH = 128;
const MAX_REPO_LENGTH = 100;
const MAX_REF_LENGTH = 255;
const UNSAFE_REF_PARTS = ['..', '//', '@{', '\\'];

export interface GithubFormInput {
  displayName: string;
  description: string;
  tags: string[];
  owner: string;
  repo: string;
  ref: string;
  paths: string[];
  githubAppClientId: string;
  githubAppClientSecret: string;
}

export type GithubFormErrors = Partial<Record<keyof GithubFormInput, string>>;

/**
 * Trim, drop empties and duplicates, and strip trailing slashes ("skills/" -> "skills", "./" -> ".").
 * A path made only of slashes is kept as typed so validation rejects it instead of it silently
 * becoming the repository root.
 */
export const normalizePaths = (paths: string[]): string[] => {
  const seen = new Set<string>();
  const normalizedPaths: string[] = [];

  for (const path of paths) {
    const trimmedPath = path.trim();
    if (!trimmedPath) continue;

    const normalizedPath = trimmedPath.replace(/\/+$/, '') || trimmedPath;
    if (seen.has(normalizedPath)) continue;
    seen.add(normalizedPath);
    normalizedPaths.push(normalizedPath);
  }

  return normalizedPaths;
};

export const normalizeTags = (tags: string[]): string[] => {
  const seen = new Set<string>();
  const normalizedTags: string[] = [];

  for (const tag of tags) {
    const normalizedTag = tag.trim();
    const comparableTag = normalizedTag.toLocaleLowerCase();
    if (!normalizedTag || seen.has(comparableTag)) continue;
    seen.add(comparableTag);
    normalizedTags.push(normalizedTag);
  }

  return normalizedTags;
};

export const isSafeRepositoryPath = (path: string): boolean => {
  if (path.startsWith('/') || path.includes('\\')) return false;
  return !path.split('/').includes('..');
};

const isSafeRef = (ref: string): boolean =>
  ref.length <= MAX_REF_LENGTH && !ref.startsWith('/') && !UNSAFE_REF_PARTS.some(part => ref.includes(part));

const isSameList = (left: string[], right: string[]): boolean =>
  left.length === right.length && left.every((value, index) => value === right[index]);

/** Validate a GitHub form. Ref is optional: blank means the backend default. */
export const validateGithubForm = (data: GithubFormInput, { requireSecret }: { requireSecret: boolean }) => {
  const errors: GithubFormErrors = {};
  const displayName = data.displayName.trim();
  const owner = data.owner.trim();
  const repo = data.repo.trim();
  const ref = data.ref.trim();
  const paths = normalizePaths(data.paths);

  if (!displayName) errors.displayName = 'Display Name is required';
  else if (displayName.length > MAX_DISPLAY_NAME_LENGTH) {
    errors.displayName = `Display Name must be ${MAX_DISPLAY_NAME_LENGTH} characters or fewer`;
  }
  if (!owner) errors.owner = 'Owner is required';
  else if (!GITHUB_OWNER_PATTERN.test(owner)) errors.owner = 'Enter a valid GitHub user or organization';
  if (!repo) errors.repo = 'Repo is required';
  else if (repo.length > MAX_REPO_LENGTH || !GITHUB_REPO_PATTERN.test(repo)) {
    errors.repo = 'Enter a valid GitHub repository name';
  }
  if (ref && !isSafeRef(ref)) errors.ref = 'Enter a safe branch, tag, or commit SHA';
  if (paths.length === 0) errors.paths = 'Add at least one repository path';
  else if (paths.some(path => !isSafeRepositoryPath(path))) {
    errors.paths = 'Paths must be safe repository-relative POSIX paths';
  }
  if (!data.githubAppClientId.trim()) errors.githubAppClientId = 'GitHub App Client ID is required';
  if (requireSecret && !data.githubAppClientSecret.trim()) {
    errors.githubAppClientSecret = 'GitHub App Client Secret is required';
  }
  return errors;
};

/** Create payload; blank optional fields are omitted so backend defaults (e.g. ref "main") apply. */
export const buildGithubCreatePayload = (form: GithubFormInput): CreateSkillSyncSourceRequest => {
  const description = form.description.trim();
  const ref = form.ref.trim();
  return {
    displayName: form.displayName.trim(),
    ...(description ? { description } : {}),
    tags: normalizeTags(form.tags),
    owner: form.owner.trim(),
    repo: form.repo.trim(),
    ...(ref ? { ref } : {}),
    paths: normalizePaths(form.paths),
    githubAppClientId: form.githubAppClientId.trim(),
    githubAppClientSecret: form.githubAppClientSecret.trim(),
  };
};

/**
 * Update payload containing only the fields that differ from the loaded source. An empty result
 * means nothing changed. A real credential change drops every user's GitHub authorization, so
 * unchanged fields are never sent (the backend also compares by value, as defense in depth).
 * - A cleared description is sent as `null` so the stored one is removed.
 * - A blank ref means the default branch, so it is sent as "main" only if the stored ref differs.
 * - The secret is write-only: it is sent whenever the user typed one.
 */
export const buildGithubUpdatePayload = (
  source: SkillSyncSourceDetail,
  form: GithubFormInput,
): UpdateSkillSyncSourceRequest => {
  const payload: UpdateSkillSyncSourceRequest = {};
  const displayName = form.displayName.trim();
  const description = form.description.trim();
  const tags = normalizeTags(form.tags);
  const owner = form.owner.trim();
  const repo = form.repo.trim();
  const ref = form.ref.trim() || DEFAULT_GITHUB_REF;
  const paths = normalizePaths(form.paths);
  const githubAppClientId = form.githubAppClientId.trim();
  const githubAppClientSecret = form.githubAppClientSecret.trim();

  if (displayName !== source.displayName) payload.displayName = displayName;
  if (description !== (source.description ?? '')) payload.description = description || null;
  if (!isSameList(tags, source.tags)) payload.tags = tags;
  if (owner !== source.owner) payload.owner = owner;
  if (repo !== source.repo) payload.repo = repo;
  if (ref !== source.ref) payload.ref = ref;
  if (!isSameList(paths, source.paths)) payload.paths = paths;
  if (githubAppClientId !== source.githubAppClientId) payload.githubAppClientId = githubAppClientId;
  if (githubAppClientSecret) payload.githubAppClientSecret = githubAppClientSecret;
  return payload;
};

export const hasGithubFormChanges = (source: SkillSyncSourceDetail, form: GithubFormInput): boolean =>
  Object.keys(buildGithubUpdatePayload(source, form)).length > 0;
