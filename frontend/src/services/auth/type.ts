export type GetAuthMeResponse = {
  username: string;
  email: string;
  scopes: string[];
  groups: string[];
  authMethod: string;
  provider: string;
  canModifyServers: boolean;
  isAdmin: boolean;
};

export type GetTokenRequest = {
  expiresInHours: number;
  description?: string;
  requestedScopes?: string[];
};

type TokenData = {
  accessToken: string;
  expiresIn: number;
  tokenType: string;
  scope: string;
};

export type GetTokenResponse = {
  success: boolean;
  tokenData: TokenData;
  userScopes: string[];
  requestedScopes: string[];
};

export enum AuthCookieKey {
  JarvisRegistrySession = 'jarvis_registry_session',
  JarvisRegistryRefresh = 'jarvis_registry_refresh',
}
