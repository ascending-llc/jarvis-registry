import { getBasePathForUrl } from '@/config';

export const APP_ROUTES = {
  login: '/login',
  root: '/',
  oauthCallback: '/oauth-callback',
  consentDownstream: '/consent/downstream',
  consentServer: '/consent/server',
  serverRegistry: '/server-registry',
  serverEdit: '/server-edit',
  agentRegistry: '/agent-registry',
  agentEdit: '/agent-edit',
  federationRegistry: '/federation-registry',
  federationEdit: '/federation-edit',
  workflowRegistry: '/workflow-registry',
  workflowEdit: '/workflow-edit',
  generateToken: '/generate-token',
} as const;

const REGISTERED_ROUTE_PATHS = new Set<string>(Object.values(APP_ROUTES));
const PUBLIC_ROUTE_PATHS = new Set<string>([APP_ROUTES.login]);

const normalizePathname = (pathname: string): string => {
  if (!pathname || pathname === '/') return '/';
  return pathname.replace(/\/+$/, '') || '/';
};

const getNormalizedBasePath = (): string => {
  const basePath = getBasePathForUrl();
  if (!basePath || basePath === '/') return '';

  const pathWithLeadingSlash = basePath.startsWith('/') ? basePath : `/${basePath}`;
  return normalizePathname(pathWithLeadingSlash);
};

export const getBrowserPath = (route: string): string => {
  const basePath = getNormalizedBasePath();
  const normalizedRoute = normalizePathname(route);
  if (normalizedRoute === '/') return basePath || '/';
  return `${basePath}${normalizedRoute}`;
};

export const getAppRoutePath = (pathname: string): string | null => {
  const normalizedPathname = normalizePathname(pathname);
  const basePath = getNormalizedBasePath();
  const comparablePathname = normalizedPathname.toLowerCase();
  const comparableBasePath = basePath.toLowerCase();
  if (!basePath) return normalizedPathname;
  if (comparablePathname === comparableBasePath) return APP_ROUTES.root;
  if (!comparablePathname.startsWith(`${comparableBasePath}/`)) return null;
  return normalizePathname(normalizedPathname.slice(basePath.length));
};

export const isProtectedBrowserPath = (pathname: string): boolean => {
  const routePath = getAppRoutePath(pathname);
  if (routePath === null) return false;

  const comparableRoutePath = routePath.toLowerCase();
  return REGISTERED_ROUTE_PATHS.has(comparableRoutePath) && !PUBLIC_ROUTE_PATHS.has(comparableRoutePath);
};

export const isLoginBrowserPath = (pathname: string): boolean =>
  getAppRoutePath(pathname)?.toLowerCase() === APP_ROUTES.login;

export const isAppRootPath = (pathname: string): boolean => getAppRoutePath(pathname) === APP_ROUTES.root;
