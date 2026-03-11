import type { ApiKeyHeaderFormat, ApiKeySource, ServerType } from '@/pages/ServerRegistryOrEdit/types';
import type { ServerConnection } from '@/services/mcp/type';

export type GetVersionResponse = {
  version: string;
};

export type GetServersRequest = {
  query?: string;
  status?: string;
  page?: string;
  perPage?: string;
};

export type OauthConfig = {
  authorizationUrl: string;
  tokenUrl: string;
  clientId?: string;
  clientSecret?: string;
  scope?: string;
};
export type ApiKeyConfig = {
  key: string;
  source: ApiKeySource;
  authorizationType: ApiKeyHeaderFormat;
  customHeader?: string;
};
export type StatusType = 'active' | 'inactive' | 'error';
export type PermissionType = {
  VIEW: boolean;
  EDIT: boolean;
  DELETE: boolean;
  SHARE: boolean;
};
export type Server = {
  id: string;
  serverName: string;
  title: string;
  description: string;
  type: ServerType;
  url: string;
  enabled: boolean;
  requiresOauth: boolean;
  connectionState: ServerConnection;
  capabilities: string;
  tools: string;
  author: string;
  headers: Record<string, string> | null;
  status: StatusType;
  path: string;
  permissions: PermissionType;
  tags: string[];
  numTools: number;
  numStars: number;
  initDuration: number;
  lastConnected: string;
  createdAt: string;
  updatedAt: string;
  isPython?: boolean;
  isOfficial?: boolean;
  oauth?: OauthConfig;
  apiKey?: ApiKeyConfig;
};

export type GetServersResponse = {
  servers: Server[];
  pagination: {
    total: number;
    page: number;
    perPage: number;
    totalPages: number;
  };
};

export type GetServersDetailResponse = Server;

export type TestServerUrlRequest = {
  url: string;
  transport: ServerType;
};
export type TestServerUrlResponse = {
  success: boolean;
  message: string;
};

export type CreateServerRequest = {
  serverName: string;
  description: string;
  path: string;
  url: string;
  tags?: string[];
  enabled: boolean;
  type: ServerType;
  oauth?: OauthConfig;
  apiKey?: ApiKeyConfig;
};

export type Tool = {
  name: string;
  description?: string;
  inputSchema?: any;
};
export type GetServerToolsResponse = {
  id: string;
  tools: Tool[];
};
