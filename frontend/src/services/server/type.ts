import type { ApiKeyHeaderFormat, ApiKeySource, ServerType } from '@/pages/ServerRegistryOrEdit/types';
import type { SERVER_CONNECTION } from '@/services/mcp/type';

export type GET_VERSION_RESPONSE = {
  version: string;
};

export type GET_SERVERS_REQUEST = {
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
  connectionState: SERVER_CONNECTION;
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

export type GET_SERVERS_RESPONSE = {
  servers: Server[];
  pagination: {
    total: number;
    page: number;
    perPage: number;
    totalPages: number;
  };
};

export type GET_SERVERS_DETAIL_RESPONSE = Server;

export type TEST_SERVER_URL_REQUEST = {
  url: string;
  transport: ServerType;
};
export type TEST_SERVER_URL_RESPONSE = {
  success: boolean;
  message: string;
};

export type CREATE_SERVER_REQUEST = {
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
export type GET_SERVER_TOOLS_RESPONSE = {
  id: string;
  tools: Tool[];
};
