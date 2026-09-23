// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import type { FederationSyncJobStatus } from '@/services/federation/type';

import { getFederationSyncViewState, useFederationSyncPolling } from './useFederationSyncPolling';

const mocks = vi.hoisted(() => ({ getFederationSyncJob: vi.fn() }));

vi.mock('@/services', () => ({ default: { FEDERATION: { getFederationSyncJob: mocks.getFederationSyncJob } } }));

const makeJob = (overrides: Partial<FederationSyncJobStatus> = {}): FederationSyncJobStatus => ({
  id: 'job-1',
  federationId: 'source-1',
  jobType: 'full_sync',
  status: 'syncing',
  phase: 'discovering',
  startedAt: null,
  finishedAt: null,
  error: null,
  ...overrides,
});

const baseInput = {
  hasServerJobId: true,
  isStarting: false,
  isPolling: false,
  pollingError: null,
  jobStatus: null,
};

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe('getFederationSyncViewState', () => {
  test('a partial_success job reads as partially completed and allows a new sync', () => {
    const view = getFederationSyncViewState({ ...baseInput, jobStatus: makeJob({ status: 'partial_success' }) });
    expect(view).toMatchObject({ kind: 'success', label: 'Sync partially completed', action: 'start' });
  });

  test('a partial_success server status carries the sync message', () => {
    const view = getFederationSyncViewState({
      ...baseInput,
      serverStatus: 'partial_success',
      syncMessage: '2 skills failed',
    });
    expect(view).toMatchObject({ kind: 'success', label: 'Sync partially completed', detail: '2 skills failed' });
  });

  test('a polling error offers Retry Status', () => {
    expect(getFederationSyncViewState({ ...baseInput, pollingError: 'timeout' })).toMatchObject({
      action: 'retry',
      actionLabel: 'Retry Status',
    });
  });

  test('a busy server with no job id offers Refresh Status', () => {
    expect(getFederationSyncViewState({ ...baseInput, serverStatus: 'pending', hasServerJobId: false })).toMatchObject({
      action: 'refresh',
      actionLabel: 'Refresh Status',
    });
  });

  test('an active poll shows the GitHub phase label and blocks new syncs', () => {
    const view = getFederationSyncViewState({
      ...baseInput,
      isPolling: true,
      jobStatus: makeJob({ phase: 'downloading' }),
    });
    expect(view).toMatchObject({ kind: 'running', label: 'Downloading repository...', action: 'none' });
  });
});

describe('useFederationSyncPolling', () => {
  test('polls through an injected fetcher instead of the federation service', async () => {
    const fetcher = vi.fn().mockResolvedValue(makeJob({ status: 'success', phase: 'completed' }));
    const onTerminal = vi.fn();
    const { result } = renderHook(() => useFederationSyncPolling(onTerminal, fetcher));

    act(() => result.current.startPolling('source-1', 'job-1'));

    await waitFor(() => expect(onTerminal).toHaveBeenCalledTimes(1));
    expect(fetcher).toHaveBeenCalledWith('source-1', 'job-1', { signal: expect.any(AbortSignal) });
    expect(mocks.getFederationSyncJob).not.toHaveBeenCalled();
    expect(onTerminal.mock.calls[0][0].status).toBe('success');
    expect(result.current.isPolling).toBe(false);
  });

  test('uses the federation service by default', async () => {
    mocks.getFederationSyncJob.mockResolvedValue(makeJob({ status: 'failed', phase: 'failed', error: 'x' }));
    const onTerminal = vi.fn();
    const { result } = renderHook(() => useFederationSyncPolling(onTerminal));

    act(() => result.current.startPolling('fed-1', 'job-1'));

    await waitFor(() => expect(onTerminal).toHaveBeenCalledTimes(1));
    expect(mocks.getFederationSyncJob).toHaveBeenCalledWith('fed-1', 'job-1', { signal: expect.any(AbortSignal) });
  });

  test('keeps polling until the job reaches a terminal status', async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(makeJob({ status: 'syncing', phase: 'applying' }))
      .mockResolvedValueOnce(makeJob({ status: 'partial_success', phase: 'completed' }));
    const onTerminal = vi.fn();
    const { result } = renderHook(() => useFederationSyncPolling(onTerminal, fetcher));

    await act(async () => {
      result.current.startPolling('source-1', 'job-1');
    });
    expect(result.current.jobStatus?.phase).toBe('applying');
    expect(onTerminal).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(onTerminal).toHaveBeenCalledTimes(1);
    expect(result.current.jobStatus?.status).toBe('partial_success');
  });

  test('reports request_failed after repeated fetch errors', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => useFederationSyncPolling(undefined, fetcher));

    await act(async () => {
      result.current.startPolling('source-1', 'job-1');
    });
    for (let attempt = 0; attempt < 4; attempt += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });
    }

    expect(fetcher).toHaveBeenCalledTimes(5);
    expect(result.current.pollingError).toBe('request_failed');
    expect(result.current.isPolling).toBe(false);
  });
});
