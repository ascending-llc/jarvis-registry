export type AuthType = 'auto' | 'apiKey' | 'oauth';
export type ApiKeySource = 'admin' | 'user';
export type ApiKeyHeaderFormat = 'bearer' | 'basic' | 'custom';
export type ServerType = 'streamable-http' | 'sse';

export interface AuthenticationConfig {
  type: AuthType;
  // API Key specific props
  source?: ApiKeySource;
  key?: string;
  authorizationType?: ApiKeyHeaderFormat;
  customHeader?: string;
  // OAuth specific props
  clientId?: string;
  clientSecret?: string;
  authorizationUrl?: string;
  tokenUrl?: string;
  scope?: string;
  useDynamicRegistration?: boolean;
}

export interface ServerConfig {
  title: string;
  description: string;
  path: string;
  url: string;
  type: ServerType;
  headers: Record<string, string> | null;
  authConfig: AuthenticationConfig;
  trustServer: boolean;
  tags?: string[];
}
