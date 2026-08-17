import API from './api';
import service from './request';

export interface ConsentScope {
  name: string;
  description: string | null;
}

export interface ConsentContext {
  client_name: string;
  client_uri: string | null;
  redirect_uri?: string | null;
  ip_address: string | null;
  registered_at: number | null;
  server_path?: string;
  server_name?: string;
  agent_path?: string;
  agent_name?: string;
  scopes?: ConsentScope[];
}

export interface ResolveDeviceCodeResponse {
  nonce: string;
}

export interface DownstreamErrorConsentContext {
  redirect_uri: string;
  error: string;
  error_description: string;
  server_path: string;
}

const MOCK_ENABLED = import.meta.env.VITE_MOCK_CONSENT_API === 'true';
const MOCK_DEVICE_NONCE = 'mock-device-nonce';

// MCP clients recognized for browser deep-link-back (matches OAuthCallback.tsx's list).
export const DEEP_LINK_BRANDS = ['cursor', 'vscode', 'claude'];

const MOCK_DOWNSTREAM_CONTEXT: ConsentContext = {
  client_name: 'Claude Desktop (mock)',
  client_uri: 'https://claude.ai',
  redirect_uri: 'https://claude.ai/callback',
  ip_address: '203.0.113.7',
  registered_at: Math.floor(Date.now() / 1000) - 120,
  server_path: 'github',
  scopes: [
    {
      name: 'mcp-proxy-ops',
      description: 'Act on your behalf to connect to and call tools on your registered MCP servers.',
    },
  ],
};

const MOCK_SERVER_CONTEXT: ConsentContext = {
  ...MOCK_DOWNSTREAM_CONTEXT,
  redirect_uri: undefined,
  server_name: 'GitHub',
};
const MOCK_AGENT_CONTEXT: ConsentContext = {
  ...MOCK_DOWNSTREAM_CONTEXT,
  redirect_uri: undefined,
  server_path: undefined,
  agent_path: '/research',
  agent_name: 'Research Agent',
};

export async function resolveDeviceCode(userCode: string): Promise<ResolveDeviceCodeResponse> {
  if (MOCK_ENABLED) {
    if (userCode === 'INVALID') {
      return Promise.reject({ detail: 'This code is invalid or has expired.' });
    }
    return { nonce: MOCK_DEVICE_NONCE };
  }
  return service.get(API.resolveDeviceCode(userCode)) as Promise<ResolveDeviceCodeResponse>;
}

export async function getDownstreamConsentContext(nonce: string): Promise<ConsentContext> {
  if (MOCK_ENABLED) return MOCK_DOWNSTREAM_CONTEXT;
  return service.get(API.getDownstreamConsent(nonce)) as Promise<ConsentContext>;
}

export async function approveDownstreamConsent(nonce: string): Promise<{ redirect_url: string }> {
  if (MOCK_ENABLED) return { redirect_url: 'https://example.com/mock-provider-redirect' };
  return service.post(API.approveDownstreamConsent, { nonce }) as Promise<{ redirect_url: string }>;
}

export interface ConsentDecisionResponse {
  status: string;
  client_branding: string | null;
}

export async function denyDownstreamConsent(nonce: string): Promise<ConsentDecisionResponse> {
  if (MOCK_ENABLED) return { status: 'denied', client_branding: null };
  return service.post(API.denyDownstreamConsent, { nonce }) as Promise<ConsentDecisionResponse>;
}

export async function getDownstreamErrorConsentContext(nonce: string): Promise<DownstreamErrorConsentContext> {
  if (MOCK_ENABLED) {
    return {
      redirect_uri: 'http://localhost:33418/callback',
      error: 'invalid_client',
      error_description: 'unknown client_id',
      server_path: 'github',
    };
  }
  return service.get(API.getDownstreamErrorConsent(nonce)) as Promise<DownstreamErrorConsentContext>;
}

export async function approveDownstreamErrorConsent(nonce: string): Promise<{ redirect_url: string }> {
  if (MOCK_ENABLED) {
    return { redirect_url: 'http://localhost:33418/callback?error=invalid_client' };
  }
  return service.post(API.approveDownstreamErrorConsent, { nonce }) as Promise<{ redirect_url: string }>;
}

export async function denyDownstreamErrorConsent(nonce: string): Promise<{ status: string }> {
  if (MOCK_ENABLED) return { status: 'denied' };
  return service.post(API.denyDownstreamErrorConsent, { nonce }) as Promise<{ status: string }>;
}

export async function getServerConsentContext(nonce: string): Promise<ConsentContext> {
  if (MOCK_ENABLED) return MOCK_SERVER_CONTEXT;
  return service.get(API.getServerConsent(nonce)) as Promise<ConsentContext>;
}

export async function approveServerConsent(nonce: string): Promise<ConsentDecisionResponse> {
  if (MOCK_ENABLED) return { status: 'ok', client_branding: null };
  return service.post(API.approveServerConsent, { nonce }) as Promise<ConsentDecisionResponse>;
}

export async function denyServerConsent(nonce: string): Promise<ConsentDecisionResponse> {
  if (MOCK_ENABLED) return { status: 'denied', client_branding: null };
  return service.post(API.denyServerConsent, { nonce }) as Promise<ConsentDecisionResponse>;
}

export async function getAgentConsentContext(nonce: string): Promise<ConsentContext> {
  if (MOCK_ENABLED) return MOCK_AGENT_CONTEXT;
  return service.get(API.getAgentConsent(nonce)) as Promise<ConsentContext>;
}

export async function approveAgentConsent(nonce: string): Promise<ConsentDecisionResponse> {
  if (MOCK_ENABLED) return { status: 'ok', client_branding: null };
  return service.post(API.approveAgentConsent, { nonce }) as Promise<ConsentDecisionResponse>;
}

export async function denyAgentConsent(nonce: string): Promise<ConsentDecisionResponse> {
  if (MOCK_ENABLED) return { status: 'denied', client_branding: null };
  return service.post(API.denyAgentConsent, { nonce }) as Promise<ConsentDecisionResponse>;
}
