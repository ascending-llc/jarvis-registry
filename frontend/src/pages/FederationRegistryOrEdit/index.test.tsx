// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, test, vi } from 'vitest';

import type { Federation } from '@/services/federation/type';
import type { SkillSyncSourceDetail } from '@/services/skillSyncSource/type';

import FederationRegistryOrEdit from './index';

const EXTERNAL_PROVIDERS_LIST_PATH = '/?tab=external';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  showToast: vi.fn(),
  refreshFederationData: vi.fn(),
  handleFederationUpdate: vi.fn(),
  getSkillSyncSource: vi.fn(),
  updateSkillSyncSource: vi.fn(),
  syncSkillSyncSource: vi.fn(),
  getFederation: vi.fn(),
  // Stable across renders, like the real hook's callbacks: the page's reset effect depends on stopPolling.
  syncHook: {
    syncView: {
      kind: 'idle',
      label: '',
      detail: null,
      tone: 'info',
      isBusy: false,
      action: 'none',
      actionLabel: 'Sync',
    },
    startSync: vi.fn(),
    runSyncAction: vi.fn(),
    stopPolling: vi.fn(),
  },
}));

vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual<typeof import('react-router-dom')>('react-router-dom')),
  useNavigate: () => mocks.navigate,
}));

vi.mock('@/services', () => ({
  default: {
    SKILL_SYNC_SOURCE: {
      getSkillSyncSource: mocks.getSkillSyncSource,
      updateSkillSyncSource: mocks.updateSkillSyncSource,
      syncSkillSyncSource: mocks.syncSkillSyncSource,
    },
    FEDERATION: { getFederation: mocks.getFederation },
  },
}));

vi.mock('@/contexts/GlobalContext', () => ({
  useGlobal: () => ({ showToast: mocks.showToast }),
}));

vi.mock('@/contexts/ServerContext', () => ({
  useServer: () => ({
    refreshFederationData: mocks.refreshFederationData,
    handleFederationUpdate: mocks.handleFederationUpdate,
  }),
}));

vi.mock('@/hooks/useExternalProviderSync', () => ({
  GITHUB_SYNC_NEEDS_AUTHORIZATION_MESSAGE: 'GitHub authorization is required before syncing.',
  useExternalProviderSync: () => mocks.syncHook,
}));

const githubSource: SkillSyncSourceDetail = {
  id: 'src-1',
  providerType: 'github',
  displayName: 'Skills',
  description: 'Team skills',
  tags: ['prod'],
  owner: 'octocat',
  repo: 'skills',
  ref: 'main',
  paths: ['skills'],
  status: 'active',
  syncStatus: 'success',
  syncMessage: null,
  stats: { skillCount: 1, fileCount: 2 },
  lastSync: null,
  permissions: { VIEW: true, EDIT: true, DELETE: true, SHARE: true },
  createdAt: '2026-09-01T00:00:00Z',
  updatedAt: '2026-09-01T00:00:00Z',
  githubAppClientId: 'Iv1.client',
  hasClientSecret: true,
  authorization: { connected: true },
  recentJobs: [],
};

const awsFederation: Federation = {
  id: 'fed-1',
  providerType: 'aws_agentcore',
  displayName: 'AgentCore',
  description: null,
  status: 'active',
  syncStatus: 'success',
  providerConfig: { region: 'us-east-1', assumeRoleArn: 'arn:aws:iam::123456789012:role/registry' },
  stats: { mcpServerCount: 0, agentCount: 0, toolCount: 0, importedTotal: 0, unimportedTotal: 0 },
  version: 1,
  createdBy: 'user-1',
  updatedBy: 'user-1',
  createdAt: '2026-09-01T00:00:00Z',
  updatedAt: '2026-09-01T00:00:00Z',
  permissions: { VIEW: true, EDIT: true, DELETE: true, SHARE: true },
};

const renderAt = (entry: string) =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <FederationRegistryOrEdit />
    </MemoryRouter>,
  );

/** Waits for the detail to load (the form replaces the spinner) and returns the description input. */
const descriptionInput = () => screen.findByLabelText('Description');

const clickButton = (name: string) => fireEvent.click(screen.getByRole('button', { name }));

const expectExitToProvidersList = async () => {
  await waitFor(() => expect(mocks.navigate).toHaveBeenCalledWith(EXTERNAL_PROVIDERS_LIST_PATH, { replace: true }));
  expect(mocks.navigate).not.toHaveBeenCalledWith(-1);
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  sessionStorage.clear();
});

describe('FederationRegistryOrEdit exit navigation', () => {
  describe('GitHub source', () => {
    const githubEditUrl = '/federation-edit?id=src-1&provider=github';

    test('Update with changes saves, then goes to the External Providers tab', async () => {
      mocks.getSkillSyncSource.mockResolvedValue(githubSource);
      mocks.updateSkillSyncSource.mockResolvedValue({ ...githubSource, description: 'Updated' });
      renderAt(githubEditUrl);

      fireEvent.change(await descriptionInput(), { target: { value: 'Updated' } });
      clickButton('Update');

      await expectExitToProvidersList();
      expect(mocks.updateSkillSyncSource).toHaveBeenCalledWith(
        'src-1',
        expect.objectContaining({ description: 'Updated' }),
      );
    });

    test('Update without changes skips the save and goes to the External Providers tab', async () => {
      mocks.getSkillSyncSource.mockResolvedValue(githubSource);
      renderAt(githubEditUrl);

      await descriptionInput();
      clickButton('Update');

      await expectExitToProvidersList();
      expect(mocks.showToast).toHaveBeenCalledWith('No changes to save', 'info');
      expect(mocks.updateSkillSyncSource).not.toHaveBeenCalled();
    });

    test('Cancel goes to the External Providers tab', async () => {
      mocks.getSkillSyncSource.mockResolvedValue(githubSource);
      renderAt(githubEditUrl);

      await descriptionInput();
      clickButton('Cancel');

      await expectExitToProvidersList();
    });

    test('Update right after the OAuth callback goes to the External Providers tab', async () => {
      mocks.getSkillSyncSource.mockResolvedValue(githubSource);
      mocks.syncSkillSyncSource.mockResolvedValue({ ok: true });
      mocks.updateSkillSyncSource.mockResolvedValue({ ...githubSource, description: 'Updated' });
      renderAt(`${githubEditUrl}&status=connected`);

      // The callback effect test-connects, then reloads the detail.
      await waitFor(() => expect(mocks.syncSkillSyncSource).toHaveBeenCalledWith('src-1', { dryRun: true }));
      await waitFor(() => expect(mocks.getSkillSyncSource).toHaveBeenCalledTimes(2));
      fireEvent.change(await descriptionInput(), { target: { value: 'Updated' } });
      clickButton('Update');

      await expectExitToProvidersList();
      expect(mocks.updateSkillSyncSource).toHaveBeenCalledWith(
        'src-1',
        expect.objectContaining({ description: 'Updated' }),
      );
    });
  });

  test('a non-GitHub provider goes back in history on Cancel', async () => {
    mocks.getFederation.mockResolvedValue(awsFederation);
    renderAt('/federation-edit?id=fed-1');

    await descriptionInput();
    clickButton('Cancel');

    expect(mocks.navigate).toHaveBeenCalledWith(-1);
    expect(mocks.navigate).not.toHaveBeenCalledWith(EXTERNAL_PROVIDERS_LIST_PATH, expect.anything());
  });
});
