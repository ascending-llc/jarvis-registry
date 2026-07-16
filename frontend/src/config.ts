// Runtime configuration loaded from config.js (injected at container startup)
// For local development, uses public/config.js default values

interface RuntimeConfig {
  BASE_PATH: string;
}

declare global {
  interface Window {
    __RUNTIME_CONFIG__?: RuntimeConfig;
  }
}

/**
 * Get base path from runtime config
 * Returns empty string for root path
 */
export const getBasePath = (): string => {
  return window.__RUNTIME_CONFIG__?.BASE_PATH ?? '';
};

/**
 * Normalize base path for Router basename
 * Returns '/' for empty/root path, otherwise the base path
 */
export const getRouterBasename = (): string => {
  const basePath = getBasePath();
  return basePath || '/';
};

/**
 * Normalize base path for use in URLs (ensure no trailing slash)
 */
export const getBasePathForUrl = (): string => {
  const basePath = getBasePath();
  return basePath.endsWith('/') ? basePath.slice(0, -1) : basePath;
};

/**
 * Capture the current in-app SPA path so login can return the user to it.
 * The returned path intentionally omits BASE_PATH because the backend appends
 * it to REGISTRY_CLIENT_URL, which may already include "/gateway".
 */
export const captureReturnPath = (): string => {
  const basePath = getBasePathForUrl();
  const pathname = window.location.pathname;
  const hasBasePath = basePath !== '' && (pathname === basePath || pathname.startsWith(`${basePath}/`));
  const appPath = hasBasePath ? pathname.slice(basePath.length) || '/' : pathname;

  return `${appPath}${window.location.search}`;
};
