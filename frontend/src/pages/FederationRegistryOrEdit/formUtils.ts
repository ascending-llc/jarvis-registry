export const normalizePaths = (paths: string[]): string[] => {
  const seen = new Set<string>();
  const normalizedPaths: string[] = [];

  for (const path of paths) {
    const trimmedPath = path.trim();
    if (!trimmedPath) continue;

    const normalizedPath = trimmedPath.replace(/\/+$/, '') || '.';
    if (!normalizedPath || seen.has(normalizedPath)) continue;
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

interface GithubFormSnapshotInput {
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

export const getGithubFormFingerprint = (data: GithubFormSnapshotInput): string =>
  JSON.stringify({
    displayName: data.displayName.trim(),
    description: data.description.trim(),
    tags: normalizeTags(data.tags),
    owner: data.owner.trim(),
    repo: data.repo.trim(),
    ref: data.ref.trim(),
    paths: normalizePaths(data.paths),
    githubAppClientId: data.githubAppClientId.trim(),
    githubAppClientSecret: data.githubAppClientSecret.trim(),
  });
