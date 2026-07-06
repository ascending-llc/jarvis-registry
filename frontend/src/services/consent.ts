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

const MOCK_ENABLED = import.meta.env.VITE_MOCK_CONSENT_API === 'true';

const MOCK_DOWNSTREAM_CONTEXT: ConsentContext = {
  client_name: 'Claude Desktop (mock)',
  client_uri: 'https://claude.ai',
  ip_address: '203.0.113.7',
  registered_at: Math.floor(Date.now() / 1000) - 120,
  server_path: 'github',
};

const MOCK_SERVER_CONTEXT: ConsentContext = { ...MOCK_DOWNSTREAM_CONTEXT, server_name: 'GitHub' };

export async function getDownstreamConsentContext(nonce: string): Promise<ConsentContext> {
  if (MOCK_ENABLED) return MOCK_DOWNSTREAM_CONTEXT;
  const { data } = await service.get(API.getDownstreamConsent(nonce));
  return data;
}

export async function approveDownstreamConsent(nonce: string): Promise<{ redirect_url: string }> {
  if (MOCK_ENABLED) return { redirect_url: 'https://example.com/mock-provider-redirect' };
  const { data } = await service.post(API.approveDownstreamConsent, { nonce });
  return data;
}

export async function getServerConsentContext(nonce: string): Promise<ConsentContext> {
  if (MOCK_ENABLED) return MOCK_SERVER_CONTEXT;
  const { data } = await service.get(API.getServerConsent(nonce));
  return data;
}

export async function approveServerConsent(nonce: string): Promise<{ status: string }> {
  if (MOCK_ENABLED) return { status: 'ok' };
  const { data } = await service.post(API.approveServerConsent, { nonce });
  return data;
}
