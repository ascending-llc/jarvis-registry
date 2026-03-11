const BASE_URL = '/api/v1';
const AUTH_BASE_URL = '/api/auth';
const MCP_BASE_URL = `${BASE_URL}/mcp`;
const SERVER_BASE_URL = `${BASE_URL}/servers`;

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

  // server
  getSemanticSearch: `${BASE_URL}/search/semantic`,
  getVersion: '/api/version',
  getServers: `${SERVER_BASE_URL}`,
  getServerDetail: (id: string) => `${SERVER_BASE_URL}/${id}`,
  testServerUrl: `${SERVER_BASE_URL}/connection`,
  createServer: `${SERVER_BASE_URL}`,
  updateServer: (id: string) => `${SERVER_BASE_URL}/${id}`,
  deleteServer: (id: string) => `${SERVER_BASE_URL}/${id}`,
  toggleServerStatus: (id: string) => `${SERVER_BASE_URL}/${id}/toggle`,
  getServerTools: (id: string) => `${SERVER_BASE_URL}/${id}/tools`,
  refreshServerHealth: (id: string) => `${SERVER_BASE_URL}/${id}/refresh`,
};

export default API;
