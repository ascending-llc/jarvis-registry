import { Dialog, Transition } from '@headlessui/react';
import {
  ArrowPathIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import IconButton from '@/components/IconButton';
import { useGlobal } from '@/contexts/GlobalContext';
import SERVICES from '@/services';
import type { GetOauthFlowStatusResponse } from '@/services/mcp/type';
import type { PendingAuthorization } from '@/services/workflow/type';

const POLL_INTERVAL_MS = 5000;

type RowState = 'unauthorized' | 'authorizing' | 'refreshing' | 'resolved' | 'failed' | 'stale';

interface ReauthRow extends PendingAuthorization {
  state: RowState;
  attempt: number;
}

type ReauthRows = Record<string, ReauthRow>;

interface PollResult {
  serverId: string;
  attempt: number;
  status: GetOauthFlowStatusResponse | null;
}

interface WorkflowReauthModalProps {
  isOpen: boolean;
  workflowName: string;
  pendingAuthorizations: PendingAuthorization[];
  onClose: () => void;
  onRetryRun: () => void;
  retrying: boolean;
}

const _createRows = (pendingAuthorizations: PendingAuthorization[], attempt: number): ReauthRows =>
  Object.fromEntries(
    pendingAuthorizations.map(authorization => [
      authorization.serverId,
      {
        ...authorization,
        state: 'unauthorized' as const,
        attempt,
      },
    ]),
  );

const _getNextRowState = (status: GetOauthFlowStatusResponse): RowState | null => {
  if (status.completed || status.status === 'completed') return 'resolved';
  if (status.failed || status.status === 'failed') return 'failed';
  if (status.status === 'not_found') return 'stale';
  return null;
};

const _applyPollResults = (rows: ReauthRows, results: PollResult[]): ReauthRows => {
  let nextRows = rows;

  for (const result of results) {
    const row = nextRows[result.serverId];
    if (!row || row.attempt !== result.attempt || !result.status) continue;

    const nextState = _getNextRowState(result.status);
    if (!nextState || nextState === row.state) continue;

    if (nextRows === rows) nextRows = { ...rows };
    nextRows[result.serverId] = { ...row, state: nextState };
  }

  return nextRows;
};

const _hasUnresolvedRows = (rows: ReauthRows): boolean =>
  Object.values(rows).some(row => row.state !== 'resolved');

const _getPollableRows = (rows: ReauthRows): ReauthRow[] =>
  Object.values(rows).filter(row => row.state !== 'resolved' && row.state !== 'refreshing');

const WorkflowReauthModal: React.FC<WorkflowReauthModalProps> = ({
  isOpen,
  workflowName,
  pendingAuthorizations,
  onClose,
  onRetryRun,
  retrying,
}) => {
  const { showToast } = useGlobal();
  const [rows, setRows] = useState<ReauthRows>({});
  const rowsRef = useRef<ReauthRows>({});
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollGenerationRef = useRef(0);
  const attemptCounterRef = useRef(0);
  const refreshInFlightRef = useRef<Set<string>>(new Set());

  const replaceRows = useCallback((nextRows: ReauthRows) => {
    rowsRef.current = nextRows;
    setRows(nextRows);
  }, []);

  const updateRow = useCallback(
    (serverId: string, updater: (row: ReauthRow) => ReauthRow) => {
      const currentRows = rowsRef.current;
      const currentRow = currentRows[serverId];
      if (!currentRow) return;

      const nextRow = updater(currentRow);
      if (nextRow === currentRow) return;
      replaceRows({ ...currentRows, [serverId]: nextRow });
    },
    [replaceRows],
  );

  useEffect(() => {
    if (!isOpen) return;
    refreshInFlightRef.current.clear();
    replaceRows(_createRows(pendingAuthorizations, ++attemptCounterRef.current));
  }, [isOpen, pendingAuthorizations, replaceRows]);

  useEffect(() => {
    if (!isOpen) return;

    const generation = ++pollGenerationRef.current;
    let cancelled = false;

    function scheduleNextPoll() {
      if (cancelled || pollGenerationRef.current !== generation) return;
      pollTimerRef.current = setTimeout(() => {
        void poll();
      }, POLL_INTERVAL_MS);
    }

    async function poll() {
      const pollableRows = _getPollableRows(rowsRef.current);
      if (pollableRows.length === 0) {
        if (_hasUnresolvedRows(rowsRef.current)) scheduleNextPoll();
        return;
      }

      const results = await Promise.all(
        pollableRows.map(async row => {
          try {
            const status = await SERVICES.MCP.getOauthFlowStatus(row.flowId);
            return { serverId: row.serverId, attempt: row.attempt, status };
          } catch {
            return { serverId: row.serverId, attempt: row.attempt, status: null };
          }
        }),
      );

      if (cancelled || pollGenerationRef.current !== generation) return;

      const nextRows = _applyPollResults(rowsRef.current, results);
      if (nextRows !== rowsRef.current) replaceRows(nextRows);
      if (_hasUnresolvedRows(nextRows)) scheduleNextPoll();
    }

    scheduleNextPoll();

    return () => {
      cancelled = true;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
      if (pollGenerationRef.current === generation) pollGenerationRef.current += 1;
    };
  }, [isOpen, pendingAuthorizations, replaceRows]);

  const handleAuthorize = (serverId: string) => {
    const row = rowsRef.current[serverId];
    if (!row?.authUrl) {
      showToast('Authorization URL is unavailable. Please retry.', 'error');
      return;
    }

    const authorizationWindow = window.open(row.authUrl, '_blank');
    if (!authorizationWindow) {
      showToast('Your browser blocked the authorization window. Please allow pop-ups and try again.', 'error');
      return;
    }

    authorizationWindow.opener = null;
    const attempt = ++attemptCounterRef.current;
    updateRow(serverId, currentRow => ({
      ...currentRow,
      state: 'authorizing',
      attempt,
    }));
  };

  const handleRefreshAndAuthorize = async (serverId: string) => {
    if (refreshInFlightRef.current.has(serverId)) return;

    const currentRow = rowsRef.current[serverId];
    if (!currentRow) return;

    const authorizationWindow = window.open('about:blank', '_blank');
    if (!authorizationWindow) {
      showToast('Your browser blocked the authorization window. Please allow pop-ups and try again.', 'error');
      return;
    }

    authorizationWindow.opener = null;
    refreshInFlightRef.current.add(serverId);
    const attempt = ++attemptCounterRef.current;
    updateRow(serverId, row => ({ ...row, state: 'refreshing', attempt }));

    try {
      const result = await SERVICES.MCP.getOauthInitiate(serverId);
      const popupIsOpen = !authorizationWindow.closed;

      updateRow(serverId, row => {
        if (row.attempt !== attempt) return row;
        return {
          ...row,
          authUrl: result.authorizationUrl,
          flowId: result.flowId,
          state: popupIsOpen ? 'authorizing' : 'unauthorized',
        };
      });

      if (popupIsOpen) authorizationWindow.location.replace(result.authorizationUrl);
      else showToast('The authorization window was closed. Click Authorize to open it again.', 'error');
    } catch (error) {
      if (!authorizationWindow.closed) authorizationWindow.close();
      updateRow(serverId, row => (row.attempt === attempt ? { ...row, state: 'stale' } : row));
      const message = error instanceof Error ? error.message : 'Failed to refresh the authorization flow';
      showToast(message, 'error');
    } finally {
      refreshInFlightRef.current.delete(serverId);
    }
  };

  const allResolved = useMemo(
    () =>
      pendingAuthorizations.length > 0 &&
      pendingAuthorizations.every(authorization => rows[authorization.serverId]?.state === 'resolved'),
    [pendingAuthorizations, rows],
  );

  const handleClose = () => {
    if (!retrying) onClose();
  };

  return (
    <Transition appear show={isOpen} as={Fragment}>
      <Dialog as='div' className='relative z-50' onClose={handleClose}>
        <Transition.Child
          as={Fragment}
          enter='ease-out duration-200'
          enterFrom='opacity-0'
          enterTo='opacity-100'
          leave='ease-in duration-150'
          leaveFrom='opacity-100'
          leaveTo='opacity-0'
        >
          <div className='fixed inset-0 bg-black/50 backdrop-blur-sm' aria-hidden='true' />
        </Transition.Child>

        <div className='fixed inset-0 overflow-y-auto'>
          <div className='flex min-h-full items-center justify-center px-4 py-6'>
            <Transition.Child
              as={Fragment}
              enter='ease-out duration-200'
              enterFrom='opacity-0 scale-95'
              enterTo='opacity-100 scale-100'
              leave='ease-in duration-150'
              leaveFrom='opacity-100 scale-100'
              leaveTo='opacity-0 scale-95'
            >
              <Dialog.Panel className='w-full max-w-[580px] overflow-hidden rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] shadow-2xl'>
                <div className='flex items-start justify-between gap-3 border-b border-[color:var(--jarvis-border-soft)] px-5 py-4'>
                  <Dialog.Title
                    as='h3'
                    className='min-w-0 break-words text-[15px] font-medium text-[var(--jarvis-text-strong)]'
                  >
                    Re-authorization required — {workflowName}
                  </Dialog.Title>
                  <IconButton
                    ariaLabel='Close'
                    tooltip='Close'
                    onClick={handleClose}
                    size='card'
                    disabled={retrying}
                    className='shrink-0 text-[var(--jarvis-muted)] hover:text-[var(--jarvis-text)]'
                  >
                    <XMarkIcon className='h-4 w-4' />
                  </IconButton>
                </div>

                <div className='p-5'>
                  <p className='mb-4 text-sm text-[var(--jarvis-subtle)]'>
                    These MCP servers need OAuth re-authorization before this run can start.
                  </p>

                  <div className='flex max-h-[50vh] flex-col gap-2 overflow-y-auto pr-1'>
                    {pendingAuthorizations.map(authorization => {
                      const row = rows[authorization.serverId];
                      const state = row?.state ?? 'unauthorized';

                      return (
                        <div
                          key={authorization.serverId}
                          className='flex items-center justify-between gap-3 rounded-md border border-[color:var(--jarvis-border)] px-3 py-2'
                        >
                          <span
                            className='min-w-0 truncate text-sm text-[var(--jarvis-text)]'
                            title={authorization.serverName}
                          >
                            {authorization.serverName}
                          </span>

                          {state === 'resolved' && (
                            <CheckCircleIcon
                              aria-label='Authorized'
                              className='h-5 w-5 shrink-0 text-[var(--jarvis-success-text)]'
                            />
                          )}

                          {(state === 'failed' || state === 'stale') && (
                            <div className='flex shrink-0 items-center gap-2'>
                              <ExclamationTriangleIcon
                                aria-label='Authorization failed'
                                className='h-5 w-5 text-[var(--jarvis-warning-text)]'
                              />
                              <button
                                type='button'
                                className='text-xs text-[var(--jarvis-primary-text)] underline transition-colors hover:text-[var(--jarvis-primary-text-hover)]'
                                onClick={() => {
                                  if (state === 'failed') handleAuthorize(authorization.serverId);
                                  else void handleRefreshAndAuthorize(authorization.serverId);
                                }}
                              >
                                Retry
                              </button>
                            </div>
                          )}

                          {state === 'refreshing' && (
                            <div className='flex shrink-0 items-center gap-2 text-xs text-[var(--jarvis-subtle)]'>
                              <ArrowPathIcon className='h-4 w-4 animate-spin' />
                              Refreshing…
                            </div>
                          )}

                          {(state === 'unauthorized' || state === 'authorizing') && (
                            <button
                              type='button'
                              className='shrink-0 rounded-md border border-[color:var(--jarvis-border)] bg-transparent px-3 py-1 text-xs text-[var(--jarvis-text)] transition-colors hover:border-[var(--jarvis-border-strong)] hover:text-[var(--jarvis-text-strong)]'
                              onClick={() => handleAuthorize(authorization.serverId)}
                            >
                              {state === 'authorizing' ? 'Open again' : 'Authorize'}
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div className='flex items-center justify-end gap-2 border-t border-[color:var(--jarvis-border-soft)] bg-[var(--jarvis-card)] px-5 py-3.5'>
                  <button
                    type='button'
                    onClick={handleClose}
                    disabled={retrying}
                    className='rounded-md border border-[color:var(--jarvis-border)] bg-transparent px-4 py-1.5 text-[13px] text-[var(--jarvis-subtle)] transition-colors hover:border-[var(--jarvis-border-strong)] hover:text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-50'
                  >
                    Cancel
                  </button>
                  <button
                    type='button'
                    onClick={onRetryRun}
                    disabled={!allResolved || retrying}
                    className='rounded-md border border-transparent bg-[var(--jarvis-primary)] px-4 py-1.5 text-[13px] font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50'
                  >
                    Retry run
                  </button>
                </div>
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition>
  );
};

export default WorkflowReauthModal;
