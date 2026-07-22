import API from './api';
import service from './request';

export interface ConsentContext {
  client_name: string;
  client_uri: string | null;
  ip_address: string | null;
  registered_at: number | null;
  server_path?: string;
  server_name?: string;
}

export interface ResolveDeviceCodeResponse {
  nonce: string;
}

const MOCK_ENABLED = import.meta.env.VITE_MOCK_CONSENT_API === 'true';
const MOCK_DEVICE_NONCE = 'mock-device-nonce';

// MCP clients recognized for browser deep-link-back (matches OAuthCallback.tsx's list).
export const DEEP_LINK_BRANDS = ['cursor', 'vscode', 'claude'];

const MOCK_DOWNSTREAM_CONTEXT: ConsentContext = {
  client_name: 'Claude Desktop (mock)',
  client_uri: 'https://claude.ai',
  ip_address: '203.0.113.7',
  registered_at: Math.floor(Date.now() / 1000) - 120,
  server_path: 'github',
};

const MOCK_SERVER_CONTEXT: ConsentContext = { ...MOCK_DOWNSTREAM_CONTEXT, server_name: 'GitHub' };

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
