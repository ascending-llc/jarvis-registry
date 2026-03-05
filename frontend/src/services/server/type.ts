import type { ApiKeyHeaderFormat, ApiKeySource, ServerType } from '@/pages/ServerRegistryOrEdit/types';
import type { SERVER_CONNECTION } from '@/services/mcp/type';

export type GET_VERSION_RESPONSE = {
  version: string;
};

export type GET_SERVERS_REQUEST = {
  query?: string;
  status?: string;
  page?: string;
  per_page?: string;
};

export type OauthConfig = {
  authorization_url: string;
  token_url: string;
  client_id?: string;
  client_secret?: string;
  scope?: string;
};
export type ApiKeyConfig = {
  key: string;
  source: ApiKeySource;
  authorization_type: ApiKeyHeaderFormat;
  custom_header?: string;
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
  server_name: string;
  title: string;
  description: string;
  type: ServerType;
  url: string;
  enabled: boolean;
  requires_oauth: boolean;
  connection_state: SERVER_CONNECTION;
  capabilities: string;
  tools: string;
  author: string;
  headers: Record<string, string> | null;
  status: StatusType;
  path: string;
  permissions: PermissionType;
  tags: string[];
  num_tools: number;
  num_stars: number;
  init_duration: number;
  last_connected: string;
  created_at: string;
  updated_at: string;
  is_python?: boolean;
  is_official?: boolean;
  oauth?: OauthConfig;
  api_key?: ApiKeyConfig;
};

export type GET_SERVERS_RESPONSE = {
  servers: Server[];
  pagination: {
    total: number;
    page: number;
    per_page: number;
    total_pages: number;
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
  server_name: string;
  description: string;
  path: string;
  url: string;
  tags?: string[];
  enabled: boolean;
  type: ServerType;
  oauth?: OauthConfig;
  api_key?: ApiKeyConfig;
};

export type Tool = {
  name: string;
  description?: string;
  input_schema?: any;
};
export type GET_SERVER_TOOLS_RESPONSE = {
  id: string;
  tools: Tool[];
};
