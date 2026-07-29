import { Dialog } from '@headlessui/react';
import {
  ClipboardDocumentIcon,
  CommandLineIcon,
  ExclamationTriangleIcon,
  InformationCircleIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import IconButton from '@/components/IconButton';
import { getBasePathForUrl } from '@/config';
import { useGlobal } from '@/contexts/GlobalContext';

const PROMPT_PLACEHOLDER = '$YOUR_PROMPT_TEXT';

interface AgentConnectionModalProps {
  agentTitle: string;
  agentPath: string;
  enabled: boolean;
  isOpen: boolean;
  onClose: () => void;
}

const buildAgentProxyUrl = (agentPath: string): string => {
  const basePath = getBasePathForUrl();
  const normalizedAgentPath = agentPath.replace(/^\/+|\/+$/g, '');
  return `${window.location.origin}${basePath}/proxy/a2a/${normalizedAgentPath}`;
};

const buildCurlCommand = (agentUrl: string, messageId: string, promptText: string): string => {
  const resolvedPromptText = promptText || PROMPT_PLACEHOLDER;

  return `curl -sS -X POST "${agentUrl}" \\
  -H "Authorization: Bearer $AUTH_TOKEN" \\
  -H "Content-Type: application/json" \\
  -H "Accept: application/json, text/event-stream" \\
  --data @- <<'EOF'
{
  "jsonrpc": "2.0",
  "id": "${messageId}",
  "method": "message/send",
  "params": {
    "message": {
      "messageId": "${messageId}",
      "role": "user",
      "kind": "message",
      "parts": [
        {
          "kind": "text",
          "text": ${JSON.stringify(resolvedPromptText)}
        }
      ]
    }
  }
}
EOF`;
};

const createMessageId = (): string => `msg-${Math.floor(Date.now() / 1000)}`;

const AgentConnectionModal: React.FC<AgentConnectionModalProps> = ({
  agentTitle,
  agentPath,
  enabled,
  isOpen,
  onClose,
}) => {
  const { showToast } = useGlobal();
  const [messageId, setMessageId] = useState(createMessageId);
  const [promptText, setPromptText] = useState('');
  const connectionReady = agentPath.trim() !== '';
  const agentUrl = useMemo(() => (connectionReady ? buildAgentProxyUrl(agentPath) : ''), [agentPath, connectionReady]);
  const curlCommand = useMemo(
    () => (agentUrl ? buildCurlCommand(agentUrl, messageId, promptText) : ''),
    [agentUrl, messageId, promptText],
  );
  const promptPlaceholderIndex = curlCommand.indexOf(PROMPT_PLACEHOLDER);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    setMessageId(createMessageId());
    setPromptText('');
  }, [isOpen]);

  const copyToClipboard = useCallback(
    async (value: string, successMessage: string) => {
      if (!value) {
        return;
      }

      try {
        await navigator.clipboard.writeText(value);
        showToast(successMessage, 'success');
      } catch (error) {
        console.error('Failed to copy agent connection instructions:', error);
        showToast('Failed to copy connection instructions', 'error');
      }
    },
    [showToast],
  );

  if (!isOpen) {
    return null;
  }

  return (
    <Dialog open={isOpen} onClose={onClose} className='relative z-50'>
      <div className='fixed inset-0 bg-black/50 backdrop-blur-sm' aria-hidden='true' />
      <div className='fixed inset-0 flex items-center justify-center overflow-y-auto px-4 py-6'>
        <Dialog.Panel className='flex max-h-[90vh] w-full max-w-[760px] flex-col overflow-hidden rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-bg)] text-[var(--jarvis-text)] shadow-2xl'>
          <div className='relative flex-shrink-0 border-b border-[color:var(--jarvis-border-soft)] bg-[var(--jarvis-bg)] px-7 py-5'>
            <div className='absolute right-4 top-4 z-10'>
              <IconButton
                ariaLabel='Close connection instructions'
                tooltip='Close'
                onClick={onClose}
                size='card'
                className='border-[color:var(--jarvis-border)] bg-white/[0.04] text-[var(--jarvis-muted)] shadow-none hover:border-[color:var(--jarvis-border-strong)] hover:bg-white/[0.08] hover:text-[var(--jarvis-text)]'
              >
                <XMarkIcon className='h-4 w-4' />
              </IconButton>
            </div>

            <div className='pr-10'>
              <Dialog.Title className='mb-1 text-base font-semibold text-[var(--jarvis-text-strong)]'>
                A2A connection
              </Dialog.Title>
              <p className='text-sm leading-6 text-[var(--jarvis-subtle)]'>
                Connect to{' '}
                <span className='rounded bg-[var(--jarvis-primary-soft)] px-2 py-1 font-mono text-xs text-[var(--jarvis-primary-text)]'>
                  {agentTitle}
                </span>{' '}
                through the Jarvis proxy.
              </p>
            </div>
          </div>

          <div className='min-h-0 overflow-y-auto px-7 py-6'>
            {!enabled && (
              <div className='mb-5 flex items-start gap-2 rounded-lg border border-[var(--jarvis-warning)]/30 bg-[var(--jarvis-warning-soft)] p-3 text-xs leading-5 text-[var(--jarvis-warning-text)]'>
                <ExclamationTriangleIcon className='mt-0.5 h-4 w-4 flex-shrink-0' />
                <span>This agent is disabled. Requests will fail until an editor enables it.</span>
              </div>
            )}

            <div className='mb-5'>
              <div className='mb-2 flex items-center justify-between gap-3'>
                <span className='text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--jarvis-muted)]'>
                  Agent URL
                </span>
                <button
                  type='button'
                  onClick={() => copyToClipboard(agentUrl, 'Agent URL copied to clipboard')}
                  disabled={!connectionReady}
                  className='inline-flex flex-shrink-0 items-center gap-1.5 rounded-md border border-[color:var(--jarvis-border)] bg-white/[0.04] px-3 py-1.5 text-xs font-medium text-[var(--jarvis-text)] transition hover:border-[var(--jarvis-primary)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-primary-text-hover)] disabled:cursor-not-allowed disabled:opacity-50'
                >
                  <ClipboardDocumentIcon className='h-4 w-4' />
                  Copy URL
                </button>
              </div>
              <div className='overflow-x-auto rounded-lg border border-white/5 bg-[#0d1117] px-4 py-3 font-mono text-xs leading-6 text-[#c9d1d9]'>
                {connectionReady ? agentUrl : 'Connection URL unavailable because the agent path is missing.'}
              </div>
            </div>

            <div className='my-5 border-t border-[color:var(--jarvis-border-soft)]' />

            <div className='mb-5'>
              <div className='mb-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--jarvis-muted)]'>
                Environment
              </div>
              <span className='inline-flex items-center gap-1.5 rounded-full border border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)] px-4 py-2 text-sm font-medium text-[var(--jarvis-primary-text-hover)]'>
                <CommandLineIcon className='h-4 w-4' />
                Curl
              </span>
            </div>

            <div className='mb-4'>
              <label htmlFor='agent-prompt' className='mb-2 block text-xs font-medium text-[var(--jarvis-text)]'>
                Prompt
              </label>
              <input
                id='agent-prompt'
                type='text'
                className='input text-sm'
                placeholder='YOUR_PROMPT_TEXT'
                value={promptText}
                onChange={event => setPromptText(event.target.value)}
              />
            </div>

            <div className='mb-3 flex items-center justify-between gap-3'>
              <span className='text-xs text-[var(--jarvis-subtle)]'>Send a JSON-RPC message to the agent.</span>
              <button
                type='button'
                onClick={() => copyToClipboard(curlCommand, 'Curl command copied to clipboard')}
                disabled={!connectionReady}
                className='inline-flex flex-shrink-0 items-center gap-1.5 rounded-md border border-[color:var(--jarvis-border)] bg-white/[0.04] px-3 py-1.5 text-xs font-medium text-[var(--jarvis-text)] transition hover:border-[var(--jarvis-primary)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-primary-text-hover)] disabled:cursor-not-allowed disabled:opacity-50'
              >
                <ClipboardDocumentIcon className='h-4 w-4' />
                Copy Curl
              </button>
            </div>

            {connectionReady ? (
              <pre className='overflow-x-auto rounded-lg border border-white/5 bg-[#0d1117] px-4 py-4 text-xs leading-6 text-[#c9d1d9]'>
                <code>
                  {!promptText && promptPlaceholderIndex !== -1 ? (
                    <>
                      {curlCommand.slice(0, promptPlaceholderIndex)}
                      <span className='text-[var(--jarvis-primary-text-hover)]'>{PROMPT_PLACEHOLDER}</span>
                      {curlCommand.slice(promptPlaceholderIndex + PROMPT_PLACEHOLDER.length)}
                    </>
                  ) : (
                    curlCommand
                  )}
                </code>
              </pre>
            ) : (
              <div className='rounded-lg border border-white/5 bg-[#0d1117] px-4 py-6 text-xs leading-6 text-[var(--jarvis-muted)]'>
                A Curl command cannot be generated without an agent path.
              </div>
            )}

            <div className='mt-4 rounded-lg border border-[rgba(124,58,237,0.15)] bg-[rgba(124,58,237,0.06)] p-3'>
              <div className='mb-1 flex items-center gap-2 text-xs font-semibold text-[var(--jarvis-primary-text)]'>
                <InformationCircleIcon className='h-4 w-4' />
                Authentication
              </div>
              <p className='text-xs leading-5 text-[var(--jarvis-muted)]'>
                Authentication requires a JWT bearer token. Set it in the{' '}
                <code className='rounded bg-[var(--jarvis-card-muted)] px-1 py-0.5 font-mono text-[var(--jarvis-text)]'>
                  AUTH_TOKEN
                </code>{' '}
                environment variable before running the command.
              </p>
            </div>
          </div>
        </Dialog.Panel>
      </div>
    </Dialog>
  );
};

export default AgentConnectionModal;
