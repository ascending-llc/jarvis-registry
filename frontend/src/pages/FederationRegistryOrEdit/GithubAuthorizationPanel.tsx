import type React from 'react';
import { FaGithub } from 'react-icons/fa';

interface GithubAuthorizationPanelProps {
  connected: boolean;
  /** EDIT permission: only editors can start the OAuth flow. */
  canConnect: boolean;
  /** Why connecting is blocked right now (e.g. unsaved changes), if it is. */
  connectDisabledReason?: string;
  onConnect: () => void;
}

/**
 * The viewer's own GitHub authorization for this source. Authorization is per user, so a source
 * that syncs fine for a teammate can still show "Not connected" here.
 */
const GithubAuthorizationPanel: React.FC<GithubAuthorizationPanelProps> = ({
  connected,
  canConnect,
  connectDisabledReason,
  onConnect,
}) => (
  <div className='mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-4'>
    <div className='min-w-0'>
      <div className='flex items-center gap-2 text-sm font-medium text-[var(--jarvis-text-strong)]'>
        <FaGithub className='h-4 w-4' />
        Your GitHub authorization
        <span
          className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium ${
            connected
              ? 'bg-[var(--jarvis-success-soft)] text-[var(--jarvis-success-text)]'
              : 'bg-[var(--jarvis-card)] text-[var(--jarvis-muted)]'
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${connected ? 'bg-[var(--jarvis-success)]' : 'bg-[var(--jarvis-subtle)]'}`}
          />
          {connected ? 'Connected' : 'Not connected'}
        </span>
      </div>
      <p className='mt-1 text-xs text-[var(--jarvis-muted)]'>
        {connected
          ? 'Syncs you start use your GitHub authorization for this source.'
          : 'Each user authorizes GitHub separately. Connect before syncing or testing this source.'}
      </p>
    </div>
    {canConnect && (
      <div className='flex shrink-0 flex-col items-end gap-1'>
        <button
          type='button'
          onClick={onConnect}
          disabled={Boolean(connectDisabledReason)}
          className='inline-flex items-center gap-2 rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-4 py-2 text-sm font-medium text-[var(--jarvis-text)] shadow-sm transition-colors hover:bg-[var(--jarvis-card-muted)] disabled:cursor-not-allowed disabled:opacity-50'
        >
          <FaGithub className='h-4 w-4' />
          {connected ? 'Reconnect GitHub' : 'Connect GitHub'}
        </button>
        {connectDisabledReason && <span className='text-xs text-[var(--jarvis-subtle)]'>{connectDisabledReason}</span>}
      </div>
    )}
  </div>
);

export default GithubAuthorizationPanel;
