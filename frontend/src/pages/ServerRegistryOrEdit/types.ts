export type AuthType = 'auto' | 'api_key' | 'oauth';
export type ApiKeySource = 'admin' | 'user';
export type ApiKeyHeaderFormat = 'bearer' | 'basic' | 'custom';
export type ServerType = 'streamable-http' | 'sse';

export interface AuthenticationConfig {
  type: AuthType;
  // API Key specific props
  source?: ApiKeySource;
  key?: string;
  authorization_type?: ApiKeyHeaderFormat;
  custom_header?: string;
  // OAuth specific props
  client_id?: string;
  client_secret?: string;
  authorization_url?: string;
  token_url?: string;
  scope?: string;
  use_dynamic_registration?: boolean;
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
