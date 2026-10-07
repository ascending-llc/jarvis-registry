// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { ServerConnection } from '@/services/mcp/type';

import ServerAuthorizationModal from './ServerAuthorizationModal';

const mocks = vi.hoisted(() => ({
  getOauthReinit: vi.fn(),
  getOauthInitiate: vi.fn(),
  getServerStatusByPolling: vi.fn(),
  showToast: vi.fn(),
}));

vi.mock('@/services', () => ({
  default: { MCP: { getOauthReinit: mocks.getOauthReinit, getOauthInitiate: mocks.getOauthInitiate } },
}));

vi.mock('@/contexts/GlobalContext', () => ({
  useGlobal: () => ({ showToast: mocks.showToast }),
}));

vi.mock('@/contexts/ServerContext', () => ({
  useServer: () => ({
    refreshServerData: vi.fn(),
    getServerStatusByPolling: mocks.getServerStatusByPolling,
    cancelPolling: vi.fn(),
  }),
}));

// Headless UI's Dialog observes its panel size; jsdom has no ResizeObserver.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const renderModal = () =>
  render(
    <ServerAuthorizationModal
      name='Google Workspace'
      serverId='server-1'
      status={ServerConnection.DISCONNECTED}
      showApiKeyDialog
      handleCancelAuth={vi.fn()}
      onCloseAuthDialog={vi.fn()}
    />,
  );

const clickAuthenticate = () => fireEvent.click(screen.getByRole('button', { name: 'Authenticate', hidden: true }));

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub);
  vi.stubGlobal('open', vi.fn());
  mocks.getOauthInitiate.mockResolvedValue({ authorizationUrl: 'https://idp.example.com/authorize' });
  mocks.getServerStatusByPolling.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe('ServerAuthorizationModal handleAuth', () => {
  test('oauth_required opens the OAuth popup without polling the leftover flow first', async () => {
    mocks.getOauthReinit.mockResolvedValue({
      success: true,
      message: 'OAuth authorization required',
      server_name: 'Google Workspace',
      requires_oauth: true,
      oauth_required: true,
    });
    renderModal();

    clickAuthenticate();

    await waitFor(() => expect(mocks.getOauthInitiate).toHaveBeenCalledWith('server-1'));
    expect(window.open).toHaveBeenCalledWith('https://idp.example.com/authorize', '_blank');
    // The only poll is the one oauthInit starts for the new flow (no callback).
    expect(mocks.getServerStatusByPolling).toHaveBeenCalledTimes(1);
    expect(mocks.getServerStatusByPolling).toHaveBeenCalledWith('server-1');
    expect(mocks.getOauthInitiate.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.getServerStatusByPolling.mock.invocationCallOrder[0],
    );
  });

  test('success without oauth_required keeps the polling path', async () => {
    mocks.getOauthReinit.mockResolvedValue({
      success: true,
      message: "Server 'Google Workspace' reinitialized successfully",
      server_name: 'Google Workspace',
      requires_oauth: true,
      oauth_required: false,
    });
    renderModal();

    clickAuthenticate();

    await waitFor(() => expect(mocks.getServerStatusByPolling).toHaveBeenCalledWith('server-1', expect.any(Function)));
    expect(mocks.getOauthInitiate).not.toHaveBeenCalled();
  });
});
