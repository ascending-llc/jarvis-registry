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
  description: string;
  scopeMethod?: 'current' | 'custom';
  customScopes?: string;
};

type TokenData = {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
  refreshExpiresIn: number;
  scope: string;
  issuedAt: number;
  description: string;
};

export type GetTokenResponse = {
  success: boolean;
  tokens: TokenData & { tokenType: string };
  keycloakUrl: string;
  realm: string;
  clientId: string;
  tokenData: TokenData;
  userScopes: string[];
  requestedScopes: string[];
};
