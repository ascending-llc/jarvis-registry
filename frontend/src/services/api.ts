const BASE_URL = '/api/v1';
const AUTH_BASE_URL = '/api/auth';
const MCP_BASE_URL = `${BASE_URL}/mcp`;
const SERVER_BASE_URL = `${BASE_URL}/servers`;
const AGENT_BASE_URL = `${BASE_URL}/agents`;
const FEDERATION_BASE_URL = `${BASE_URL}/federations`;
const WORKFLOW_BASE_URL = `${BASE_URL}/workflows`;

const API = {
  // auth
  logout: '/redirect/logout',
  refreshToken: '/redirect/refresh',
  getAuthProviders: `${AUTH_BASE_URL}/providers`,
  getAuthMe: `${AUTH_BASE_URL}/me`,
  getToken: `${BASE_URL}/tokens/generate`,

  // mcp
  getServerStatusById: (id: string) => `${MCP_BASE_URL}/connection/status/${id}`,
  getOauthInitiate: (id: string) => `${MCP_BASE_URL}/${id}/oauth/initiate`,
  getOauthReinit: (id: string) => `${MCP_BASE_URL}/${id}/reinitialize`,
  cancelAuth: (id: string) => `${MCP_BASE_URL}/oauth/cancel/${id}`,
  revokeAuth: (id: string) => `${MCP_BASE_URL}/oauth/token/${id}`,
  getDiscover: `${MCP_BASE_URL}/oauth/discover`,

  // consent (as-1522 / as-1524 / as-1727 / as-1728)
  resolveDeviceCode: (userCode: string) =>
    `${MCP_BASE_URL}/consent/device/resolve?user_code=${encodeURIComponent(userCode)}`,
  getDownstreamConsent: (nonce: string) => `${MCP_BASE_URL}/consent/downstream?nonce=${encodeURIComponent(nonce)}`,
  approveDownstreamConsent: `${MCP_BASE_URL}/consent/downstream`,
  denyDownstreamConsent: `${MCP_BASE_URL}/consent/downstream/deny`,
  getServerConsent: (nonce: string) => `${MCP_BASE_URL}/consent/server?nonce=${encodeURIComponent(nonce)}`,
  approveServerConsent: `${MCP_BASE_URL}/consent/server`,
  denyServerConsent: `${MCP_BASE_URL}/consent/server/deny`,

  // server
  getSearch: `${BASE_URL}/search`,
  getVersion: '/api/version',
  getServers: `${SERVER_BASE_URL}`,
  getServerDetail: (id: string) => `${SERVER_BASE_URL}/${id}`,
  testServerUrl: `${SERVER_BASE_URL}/connection`,
  createServer: `${SERVER_BASE_URL}`,
  updateServer: (id: string) => `${SERVER_BASE_URL}/${id}`,
  deleteServer: (id: string) => `${SERVER_BASE_URL}/${id}`,
  toggleServerStatus: (id: string) => `${SERVER_BASE_URL}/${id}/toggle`,
  getServerTools: (id: string) => `${SERVER_BASE_URL}/${id}/tools`,
  refreshServer: (id: string) => `${SERVER_BASE_URL}/${id}/refresh`,

  // agent
  getAgentsList: `${AGENT_BASE_URL}`,
  getAgentState: `${AGENT_BASE_URL}/state`,
  getAgentDetail: (id: string) => `${AGENT_BASE_URL}/${id}`,
  createAgent: `${AGENT_BASE_URL}`,
  updateAgent: (id: string) => `${AGENT_BASE_URL}/${id}`,
  deleteAgent: (id: string) => `${AGENT_BASE_URL}/${id}`,
  toggleAgentState: (id: string) => `${AGENT_BASE_URL}/${id}/toggle`,
  getAgentSkills: (id: string) => `${AGENT_BASE_URL}/${id}/skills`,
  getWellKnownAgentCards: `${AGENT_BASE_URL}/.well-known/agent-cards`,
  refreshAgent: (id: string) => `${AGENT_BASE_URL}/${id}/refresh`,

  // federation
  getFederations: `${FEDERATION_BASE_URL}`,
  getFederationDetail: (id: string) => `${FEDERATION_BASE_URL}/${id}`,
  createFederation: `${FEDERATION_BASE_URL}`,
  updateFederation: (id: string) => `${FEDERATION_BASE_URL}/${id}`,
  deleteFederation: (id: string) => `${FEDERATION_BASE_URL}/${id}`,
  syncFederation: (id: string) => `${FEDERATION_BASE_URL}/${id}/sync`,
  getFederationSyncJob: (federationId: string, jobId: string) => `${FEDERATION_BASE_URL}/${federationId}/jobs/${jobId}`,

  // workflow
  getWorkflowsList: `${WORKFLOW_BASE_URL}`,
  getWorkflowDetail: (id: string) => `${WORKFLOW_BASE_URL}/${id}`,
  createWorkflow: `${WORKFLOW_BASE_URL}`,
  updateWorkflow: (id: string) => `${WORKFLOW_BASE_URL}/${id}`,
  deleteWorkflow: (id: string) => `${WORKFLOW_BASE_URL}/${id}`,
  toggleWorkflowState: (id: string) => `${WORKFLOW_BASE_URL}/${id}/toggle`,
  triggerWorkflowRun: (id: string) => `${WORKFLOW_BASE_URL}/${id}/runs`,
  getWorkflowRunsList: (id: string) => `${WORKFLOW_BASE_URL}/${id}/runs`,
  getWorkflowRunDetail: (id: string, runId: string) => `${WORKFLOW_BASE_URL}/${id}/runs/${runId}`,
  replayWorkflowRun: (id: string, runId: string) => `${WORKFLOW_BASE_URL}/${id}/runs/${runId}/replay`,
  rerunWorkflowNode: (id: string, runId: string, nodeId: string) =>
    `${WORKFLOW_BASE_URL}/${id}/runs/${runId}/nodes/${nodeId}/rerun`,

  // acl (permissions)
  searchPrincipals: `${BASE_URL}/permissions/search-principals`,
  getResourceRoles: (resourceType: string) => `${BASE_URL}/permissions/${resourceType}/roles`,
  getResourcePermissions: (resourceType: string, resourceId: string) =>
    `${BASE_URL}/permissions/${resourceType}/${resourceId}`,
  updateResourcePermissions: (resourceType: string, resourceId: string) =>
    `${BASE_URL}/permissions/${resourceType}/${resourceId}`,
};

export default API;
