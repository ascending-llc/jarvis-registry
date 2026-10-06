const URL_BASE = 'https://skill-file.local/';

const normalizePath = (path: string): string => path.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');

export const resolveSkillFilePath = (
  href: string | undefined,
  availableFilePaths: readonly string[],
): string | null => {
  if (!href) return null;

  let linkedPath: string;
  try {
    const url = new URL(href, URL_BASE);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    linkedPath = normalizePath(decodeURIComponent(url.pathname));
  } catch {
    return null;
  }

  let matchedPath: string | null = null;
  let matchedPathLength = -1;

  for (const availablePath of availableFilePaths) {
    const normalizedAvailablePath = normalizePath(availablePath);
    if (!normalizedAvailablePath) continue;

    const matches = linkedPath === normalizedAvailablePath || linkedPath.endsWith(`/${normalizedAvailablePath}`);
    if (matches && normalizedAvailablePath.length > matchedPathLength) {
      matchedPath = availablePath;
      matchedPathLength = normalizedAvailablePath.length;
    }
  }

  return matchedPath;
};
