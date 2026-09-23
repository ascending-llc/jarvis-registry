// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import type { SyncStatus } from '@/services/federation/type';

import { GITHUB_SYNC_NEEDS_AUTHORIZATION_MESSAGE, useExternalProviderSync } from './useExternalProviderSync';

const mocks = vi.hoisted(() => ({
  syncSkillSyncSource: vi.fn(),
  syncFederation: vi.fn(),
  getFederationSyncJob: vi.fn(),
  getSkillSyncJobAsFederation: vi.fn(),
  confirmGithubAuthorizationRedirect: vi.fn(),
  showToast: vi.fn(),
}));

vi.mock('@/services', () => ({
  default: {
    SKILL_SYNC_SOURCE: { syncSkillSyncSource: mocks.syncSkillSyncSource },
    FEDERATION: { syncFederation: mocks.syncFederation, getFederationSyncJob: mocks.getFederationSyncJob },
  },
}));
vi.mock('@/services/externalProvider/sync', () => ({
  getSkillSyncJobAsFederation: mocks.getSkillSyncJobAsFederation,
}));
vi.mock('@/services/externalProvider/githubAuthorization', () => ({
  confirmGithubAuthorizationRedirect: mocks.confirmGithubAuthorizationRedirect,
}));
vi.mock('@/contexts/GlobalContext', () => ({
  useGlobal: () => ({ showToast: mocks.showToast }),
}));

interface HookOptions {
  providerId: string | null;
  isGithub: boolean;
  canEdit: boolean;
  serverStatus?: SyncStatus;
  serverJobId?: string | null;
}

const renderSyncHook = (overrides: Partial<HookOptions> = {}) => {
  const onSettled = vi.fn();
  const view = renderHook((options: HookOptions) => useExternalProviderSync({ ...options, onSettled }), {
    initialProps: { providerId: 'source-1', isGithub: true, canEdit: true, serverStatus: 'success', ...overrides },
  });
  return { ...view, onSettled };
};

const deferred = () => {
  let resolve!: (value: unknown) => void;
  const promise = new Promise(res => {
    resolve = res;
  });
  return { promise, resolve };
};

const pendingJob = (id: string) => ({ id, federationId: 'source-1', status: 'pending', phase: 'queued' });

beforeEach(() => {
  // Polling calls never settle unless a test says otherwise, so no timers are left running.
  mocks.getSkillSyncJobAsFederation.mockReturnValue(new Promise(() => {}));
  mocks.getFederationSyncJob.mockReturnValue(new Promise(() => {}));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('useExternalProviderSync', () => {
  test('starts a GitHub sync, then toasts and polls the returned job', async () => {
    mocks.syncSkillSyncSource.mockResolvedValue({ job: pendingJob('job-1'), needsAuthorization: false });
    const { result } = renderSyncHook();

    let started = false;
    await act(async () => {
      started = await result.current.startSync();
    });

    expect(started).toBe(true);
    expect(mocks.syncSkillSyncSource).toHaveBeenCalledWith('source-1', { dryRun: false });
    expect(mocks.showToast).toHaveBeenCalledWith('Sync started in background', 'info');
    expect(mocks.getSkillSyncJobAsFederation).toHaveBeenCalledWith('source-1', 'job-1', expect.any(Object));
    expect(result.current.isPolling).toBe(true);
  });

  test('starts an AWS/Azure sync through the federation service', async () => {
    mocks.syncFederation.mockResolvedValue(pendingJob('job-9'));
    const { result } = renderSyncHook({ isGithub: false });

    await act(async () => {
      await result.current.startSync();
    });

    expect(mocks.syncFederation).toHaveBeenCalledWith('source-1');
    expect(mocks.syncSkillSyncSource).not.toHaveBeenCalled();
    expect(mocks.getFederationSyncJob).toHaveBeenCalledWith('source-1', 'job-9', expect.any(Object));
  });

  test('does not toast "started" when the start request fails', async () => {
    mocks.syncSkillSyncSource.mockRejectedValue({ detail: 'Skill sync source already has an active sync' });
    const { result } = renderSyncHook();

    await act(async () => {
      await result.current.startSync();
    });

    expect(mocks.showToast).toHaveBeenCalledTimes(1);
    expect(mocks.showToast).toHaveBeenCalledWith('Skill sync source already has an active sync', 'error');
    expect(result.current.isPolling).toBe(false);
    expect(result.current.syncView.kind).not.toBe('starting');
  });

  test('asks before redirecting to GitHub when authorization is needed', async () => {
    mocks.syncSkillSyncSource.mockResolvedValue({ job: null, needsAuthorization: true });
    mocks.confirmGithubAuthorizationRedirect.mockReturnValue(true);
    const { result } = renderSyncHook();

    let started = true;
    await act(async () => {
      started = await result.current.startSync();
    });

    expect(started).toBe(false);
    expect(mocks.confirmGithubAuthorizationRedirect).toHaveBeenCalledWith('source-1', 'sync');
    expect(mocks.showToast).not.toHaveBeenCalled();
  });

  test('declining the GitHub prompt stays on the page with an explanation', async () => {
    mocks.syncSkillSyncSource.mockResolvedValue({ job: null, needsAuthorization: true });
    mocks.confirmGithubAuthorizationRedirect.mockReturnValue(false);
    const { result } = renderSyncHook();

    await act(async () => {
      await result.current.startSync();
    });

    expect(mocks.showToast).toHaveBeenCalledWith(GITHUB_SYNC_NEEDS_AUTHORIZATION_MESSAGE, 'error');
  });

  test('never offers a redirect when redirects are disallowed (after an OAuth callback)', async () => {
    mocks.syncSkillSyncSource.mockResolvedValue({ job: null, needsAuthorization: true });
    const { result } = renderSyncHook();

    await act(async () => {
      await result.current.startSync({ allowAuthorizationRedirect: false });
    });

    expect(mocks.confirmGithubAuthorizationRedirect).not.toHaveBeenCalled();
    expect(mocks.showToast).toHaveBeenCalledWith(GITHUB_SYNC_NEEDS_AUTHORIZATION_MESSAGE, 'error');
  });

  test('ignores a second start while the first request is in flight', async () => {
    const start = deferred();
    mocks.syncSkillSyncSource.mockReturnValue(start.promise);
    const { result } = renderSyncHook();

    let first!: Promise<boolean>;
    act(() => {
      first = result.current.startSync();
    });
    expect(result.current.syncView.kind).toBe('starting');
    let second = true;
    await act(async () => {
      second = await result.current.startSync();
    });
    await act(async () => {
      start.resolve({ job: pendingJob('job-1'), needsAuthorization: false });
      await first;
    });

    expect(second).toBe(false);
    expect(mocks.syncSkillSyncSource).toHaveBeenCalledTimes(1);
  });

  test('drops the result of a request made for a previous provider', async () => {
    const start = deferred();
    mocks.syncSkillSyncSource.mockReturnValue(start.promise);
    const { result, rerender } = renderSyncHook();

    let pending!: Promise<boolean>;
    act(() => {
      pending = result.current.startSync();
    });
    rerender({ providerId: 'source-2', isGithub: true, canEdit: true, serverStatus: 'success' });
    await act(async () => {
      start.resolve({ job: pendingJob('job-1'), needsAuthorization: false });
      await pending;
    });

    expect(mocks.showToast).not.toHaveBeenCalled();
    expect(mocks.getSkillSyncJobAsFederation).not.toHaveBeenCalled();
  });

  test('resumes polling a job the server reports as in progress', () => {
    const { result } = renderSyncHook({ serverStatus: 'syncing', serverJobId: 'job-7' });

    expect(mocks.getSkillSyncJobAsFederation).toHaveBeenCalledWith('source-1', 'job-7', expect.any(Object));
    expect(result.current.isPolling).toBe(true);
    expect(result.current.syncView.action).toBe('none');
  });

  test('toasts and settles when the polled job finishes', async () => {
    mocks.getSkillSyncJobAsFederation.mockResolvedValue({
      id: 'job-7',
      federationId: 'source-1',
      status: 'partial_success',
      phase: 'completed',
    });
    const { onSettled } = renderSyncHook({ serverStatus: 'syncing', serverJobId: 'job-7' });

    await waitFor(() => expect(onSettled).toHaveBeenCalledTimes(1));
    expect(mocks.showToast).toHaveBeenCalledWith('Sync completed with some errors', 'info');
  });

  test('runSyncAction starts a sync for editors', async () => {
    mocks.syncSkillSyncSource.mockResolvedValue({ job: pendingJob('job-1'), needsAuthorization: false });
    const { result } = renderSyncHook();

    await act(async () => {
      result.current.runSyncAction();
    });

    expect(mocks.syncSkillSyncSource).toHaveBeenCalledTimes(1);
  });

  test('runSyncAction does nothing without EDIT permission', async () => {
    const { result } = renderSyncHook({ canEdit: false });

    await act(async () => {
      result.current.runSyncAction();
    });

    expect(mocks.syncSkillSyncSource).not.toHaveBeenCalled();
  });

  test('runSyncAction refreshes when the server is busy but reports no job', async () => {
    const { result, onSettled } = renderSyncHook({ serverStatus: 'pending', serverJobId: null });
    expect(result.current.syncView.action).toBe('refresh');

    await act(async () => {
      result.current.runSyncAction();
    });

    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(mocks.syncSkillSyncSource).not.toHaveBeenCalled();
  });
});
