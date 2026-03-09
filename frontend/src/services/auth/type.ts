export type GET_AUTH_ME_RESPONSE = {
  username: string;
  email: string;
  scopes: string[];
  groups: string[];
  authMethod: string;
  provider: string;
  canModifyServers: boolean;
  isAdmin: boolean;
};

export type GET_TOKEN_REQUEST = {
  expiresInHours: number;
  description: string;
  scopeMethod?: 'current' | 'custom';
  customScopes?: string;
};

type TOKEN_DATA = {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
  refreshExpiresIn: number;
  scope: string;
  issuedAt: number;
  description: string;
};

export type GET_TOKEN_RESPONSE = {
  success: boolean;
  tokens: TOKEN_DATA & { tokenType: string };
  keycloakUrl: string;
  realm: string;
  clientId: string;
  tokenData: TOKEN_DATA;
  userScopes: string[];
  requestedScopes: string[];
};
