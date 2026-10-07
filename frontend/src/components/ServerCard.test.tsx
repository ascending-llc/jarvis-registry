// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import type { ServerInfo } from '@/contexts/ServerContext';
import { ServerConnection } from '@/services/mcp/type';

import ServerCard from './ServerCard';

const mocks = vi.hoisted(() => ({
  toggleServerStatus: vi.fn(),
  refreshServer: vi.fn(),
  showToast: vi.fn(),
  handleServerUpdate: vi.fn(),
}));

vi.mock('@/services', () => ({
  default: { SERVER: { toggleServerStatus: mocks.toggleServerStatus, refreshServer: mocks.refreshServer } },
}));

vi.mock('@/contexts/GlobalContext', () => ({
  useGlobal: () => ({ showToast: mocks.showToast }),
}));

vi.mock('@/contexts/ServerContext', () => ({
  useServer: () => ({
    cancelPolling: vi.fn(),
    refreshServerData: vi.fn(),
    handleServerUpdate: mocks.handleServerUpdate,
  }),
}));

vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));

// Child modals have their own tests; stub them so only ServerCard's handlers run here.
vi.mock('./ServerAuthorizationModal', () => ({ default: () => <div data-testid='auth-modal' /> }));
vi.mock('./ServerToolsModal', () => ({ default: () => null }));
vi.mock('./ServerConfigModal', () => ({ default: () => null }));

const OAUTH_REQUIRED_MESSAGE = "Authorization required: connect your account to 'google-workspace' first.";
const oauthRequiredRejection = { detail: { error: 'oauth_required', message: OAUTH_REQUIRED_MESSAGE } };

const server: ServerInfo = {
  id: 'server-1',
  name: 'google-workspace',
  title: 'Google Workspace',
  permissions: { VIEW: true, EDIT: true, DELETE: false, SHARE: false },
  path: '/google-workspace',
  enabled: false,
  connectionState: ServerConnection.DISCONNECTED,
  requiresOauth: true,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ServerCard oauth_required handling', () => {
  test('enable rejected with oauth_required shows an info toast and opens the authorization modal', async () => {
    mocks.toggleServerStatus.mockRejectedValue(oauthRequiredRejection);
    render(<ServerCard server={server} />);

    fireEvent.click(screen.getByRole('checkbox'));

    await waitFor(() => expect(mocks.showToast).toHaveBeenCalledWith(OAUTH_REQUIRED_MESSAGE, 'info'));
    expect(screen.getByTestId('auth-modal')).toBeTruthy();
    expect(mocks.showToast).not.toHaveBeenCalledWith(expect.anything(), 'error');
    expect(mocks.handleServerUpdate).not.toHaveBeenCalled();
  });

  test('refresh rejected with oauth_required shows an info toast and opens the authorization modal', async () => {
    mocks.refreshServer.mockRejectedValue(oauthRequiredRejection);
    render(<ServerCard server={server} />);

    fireEvent.click(screen.getByRole('button', { name: 'Refresh health status' }));

    await waitFor(() => expect(mocks.showToast).toHaveBeenCalledWith(OAUTH_REQUIRED_MESSAGE, 'info'));
    expect(screen.getByTestId('auth-modal')).toBeTruthy();
    expect(mocks.showToast).not.toHaveBeenCalledWith(expect.anything(), 'error');
  });

  test('other toggle failures keep the error toast and do not open the modal', async () => {
    mocks.toggleServerStatus.mockRejectedValue({
      detail: 'Failed to fetch tools from server. Server remains disabled.',
    });
    render(<ServerCard server={server} />);

    fireEvent.click(screen.getByRole('checkbox'));

    await waitFor(() =>
      expect(mocks.showToast).toHaveBeenCalledWith(
        'Failed to fetch tools from server. Server remains disabled.',
        'error',
      ),
    );
    expect(screen.queryByTestId('auth-modal')).toBeNull();
  });
});
