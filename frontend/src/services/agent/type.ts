export type GetAgentsListRequest = {
  query?: string;
  status?: string;
  page?: string;
  perPage?: string;
};

export type AgentStatus = 'active' | 'inactive' | 'error';
export type AgentPermissionType = {
  VIEW: boolean;
  EDIT: boolean;
  DELETE: boolean;
  SHARE: boolean;
};

export type AgentItem = {
  id: string;
  path: string;
  name: string;
  description: string;
  url: string;
  version: string;
  protocolVersion: string;
  tags: string[];
  numSkills: number;
  skills?: AgentSkillItem[];
  enabled: boolean;
  status: AgentStatus;
  permissions: AgentPermissionType;
  author: string;
  createdAt: string;
  updatedAt: string;
  config?: {
    title?: string;
    description?: string;
    type?: string;
  };
};

export type GetAgentsListResponse = {
  agents: AgentItem[];
  pagination: {
    total: number;
    page: number;
    perPage: number;
    totalPages: number;
  };
};

export type AgentCapabilities = {
  streaming: boolean;
  pushNotifications: boolean;
};
export type AgentSkillItem = {
  id: string;
  name: string;
  description: string;
  tags: string[];
  inputModes: string[];
  outputModes: string[];
  examples?: string[];
};

export type AgentSecuritySchemes = {
  bearer: {
    type: string;
    scheme: string;
  };
};

export type AgentWellKnown = {
  enabled: boolean;
  url: string;
  lastSyncAt: string;
  lastSyncStatus: string;
  lastSyncVersion: string;
};

export type AgentProvider = {
  organization: string;
  url: string;
};
export type Agent = {
  id: string;
  path: string;
  name: string;
  description: string;
  url: string;
  version: string;
  protocolVersion: string;
  capabilities: AgentCapabilities;
  skills: AgentSkillItem[];
  securitySchemes: AgentSecuritySchemes;
  preferredTransport: string;
  defaultInputModes: string[];
  defaultOutputModes: string[];
  provider: AgentProvider;
  tags: string[];
  status: AgentStatus;
  enabled: boolean;
  permissions: AgentPermissionType;
  author: string;
  wellKnown: AgentWellKnown;
  createdAt: string;
  updatedAt: string;
  config?: {
    title?: string;
    description?: string;
    type?: string;
  };
};

export type GetAgentDetailResponse = Agent;

export type GetAgentStateResponse = {
  totalAgents: number;
  enabledAgents: number;
  disabledAgents: number;
  byStatus: {
    active: number;
    inactive: number;
    error: number;
  };
  byTransport: {
    'HTTP+JSON': number;
    JSONRPC: number;
    GRPC: number;
  };
  totalSkills: number;
  averageSkillsPerAgent: number;
};

export type CreateAgentRequest = {
  path: string;
  title: string;
  description?: string;
  url: string;
  type: string;
};
export type UpdateAgentRequest = {
  path?: string;
  title?: string;
  description?: string;
  url?: string;
  type?: string;
  enabled?: boolean;
};

export type ToggleAgentStateRequest = {
  enabled: boolean;
};

export type GetAgentSkillsResponse = {
  agentId: string;
  agentName: string;
  skills: AgentSkillItem[];
  totalSkills: number;
};

export type GetWellKnownAgentCardsResponse = {
  name: string;
  description: string;
  url: string;
  version: string;
  protocolVersion: string;
  capabilities: AgentCapabilities;
  preferredTransport: string;
  provider: AgentProvider;
  skills: AgentSkillItem[];
  securitySchemes: AgentSecuritySchemes;
  defaultInputModes: string[];
  defaultOutputModes: string[];
};
