import { ClipboardDocumentIcon, DocumentTextIcon, InformationCircleIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { getBasePath } from '@/config';
import IconButton from '@/components/IconButton';
import { useGlobal } from '@/contexts/GlobalContext';
import type { ServerInfo } from '@/contexts/ServerContext';

type IDE = 'vscode' | 'cursor' | 'cline' | 'claude-code';

interface ServerConfigModalProps {
  server?: ServerInfo;
  isOpen: boolean;
  onClose: () => void;
  configScope?: 'registry' | 'server';
}

interface IDEOption {
  id: IDE;
  label: string;
  hint: string;
  filePath: string;
  rootKey: 'servers' | 'mcpServers';
}

const IDE_OPTIONS: IDEOption[] = [
  {
    id: 'vscode',
    label: 'VS Code',
    hint: 'Configuration optimized for VS Code MCP extension',
    filePath: '.vscode/mcp.json',
    rootKey: 'servers',
  },
  {
    id: 'cursor',
    label: 'Cursor',
    hint: 'Configuration optimized for Cursor MCP settings',
    filePath: '.cursor/mcp.json',
    rootKey: 'mcpServers',
  },
  {
    id: 'cline',
    label: 'Cline',
    hint: 'Configuration optimized for Cline MCP settings',
    filePath: '.cline/mcp_settings.json',
    rootKey: 'mcpServers',
  },
  {
    id: 'claude-code',
    label: 'Claude Code',
    hint: 'Configuration optimized for Claude Code MCP settings',
    filePath: '.claude.json',
    rootKey: 'mcpServers',
  },
];

const REGISTRY_SERVER_NAME = 'jarvis-registry';
const REGISTRY_SERVER_URL = 'https://jarvis.ascendingdc.com/gateway/proxy/mcpgw/mcp';
const joinUrlPath = (...segments: string[]) =>
  segments
    .map(segment => segment.replace(/^\/+|\/+$/g, ''))
    .filter(Boolean)
    .join('/');

const ServerConfigModal: React.FC<ServerConfigModalProps> = ({ server, isOpen, onClose, configScope = 'server' }) => {
  const { showToast } = useGlobal();
  const [selectedIDE, setSelectedIDE] = useState<IDE>('vscode');

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const selectedOption = useMemo(
    () => IDE_OPTIONS.find(option => option.id === selectedIDE) ?? IDE_OPTIONS[0],
    [selectedIDE],
  );

  const serverName = configScope === 'registry' ? REGISTRY_SERVER_NAME : server?.name || 'server';
  const title = configScope === 'registry' ? 'Jarvis Registry' : server?.name || 'Server';
  const subtitle =
    configScope === 'registry'
      ? 'Connect to Jarvis Registry from your preferred Copilot environment.'
      : `Connect to ${serverName} from your preferred Copilot environment.`;

  const generateMCPConfig = useCallback(() => {
    const currentUrl = new URL(window.location.origin);
    const basePath = getBasePath();
    const normalizedPath = server?.path || '';
    const url =
      configScope === 'registry'
        ? REGISTRY_SERVER_URL
        : `${currentUrl.protocol}//${currentUrl.host}/${joinUrlPath(basePath, 'proxy/server', normalizedPath, 'mcp')}`;

    return {
      [selectedOption.rootKey]: {
        [serverName]: {
          type: 'http',
          url,
        },
      },
    };
  }, [configScope, selectedOption.rootKey, server?.path, serverName]);

  const configText = useMemo(() => JSON.stringify(generateMCPConfig(), null, 2), [generateMCPConfig]);

  const copyConfigToClipboard = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(configText);
      showToast?.('Configuration copied to clipboard', 'success');
    } catch (error) {
      console.error('Failed to copy to clipboard:', error);
      showToast?.('Failed to copy configuration', 'error');
    }
  }, [configText, showToast]);

  if (!isOpen) {
    return null;
  }

  return (
    <div
      className='fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4 py-6 backdrop-blur-sm'
      onClick={onClose}
    >
      <div
        className='relative w-full max-w-[720px] rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-bg)] p-7 text-[var(--jarvis-text)] shadow-2xl'
        onClick={event => event.stopPropagation()}
      >
        <div className="absolute right-4 top-4 z-10">
          <IconButton
            ariaLabel="Close configuration modal"
            tooltip="Close"
            onClick={onClose}
            size="card"
            className="border-[color:var(--jarvis-border)] bg-white/[0.04] text-[var(--jarvis-muted)] hover:border-[color:var(--jarvis-border-strong)] hover:bg-white/[0.08] hover:text-[var(--jarvis-text)] shadow-none"
          >
            <XMarkIcon className="h-4 w-4" />
          </IconButton>
        </div>

        <div className='pr-10'>
          <h3 className='mb-1 text-base font-semibold text-[var(--jarvis-text-strong)]'>MCP configuration</h3>
          <p className='mb-6 text-sm leading-6 text-[var(--jarvis-subtle)]'>
            {configScope === 'registry' ? (
              subtitle
            ) : (
              <>
                Connect to{' '}
                <span className='rounded bg-[var(--jarvis-primary-soft)] px-2 py-1 font-mono text-xs text-[var(--jarvis-primary-text)]'>
                  {title}
                </span>{' '}
                from your preferred Copilot environment.
              </>
            )}
          </p>
        </div>

        <div className='mb-5'>
          <div className='mb-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--jarvis-muted)]'>
            Environment
          </div>
          <div className='flex flex-wrap gap-2'>
            {IDE_OPTIONS.map(option => (
              <button
                key={option.id}
                type='button'
                onClick={() => setSelectedIDE(option.id)}
                className={`rounded-full border px-4 py-2 text-sm font-medium transition ${
                  selectedIDE === option.id
                    ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text-hover)]'
                    : 'border-[color:var(--jarvis-border)] bg-white/[0.03] text-[var(--jarvis-muted)] hover:border-[color:var(--jarvis-border-strong)] hover:bg-white/[0.06] hover:text-[var(--jarvis-text)]'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <p className='mt-2 text-xs text-[var(--jarvis-faint)]'>{selectedOption.hint}</p>
        </div>

        <div className='my-5 border-t border-[color:var(--jarvis-border-soft)]' />

        <div className='mb-3 flex items-center justify-between gap-3'>
          <div className='flex min-w-0 items-center gap-2 text-xs text-[var(--jarvis-subtle)]'>
            <DocumentTextIcon className='h-4 w-4 flex-shrink-0' />
            <span className='truncate font-mono'>{selectedOption.filePath}</span>
          </div>

          <button
            type='button'
            onClick={copyConfigToClipboard}
            className='inline-flex flex-shrink-0 items-center gap-1.5 rounded-md border border-[color:var(--jarvis-border)] bg-white/[0.04] px-3 py-1.5 text-xs font-medium text-[var(--jarvis-text)] transition hover:border-[var(--jarvis-primary)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-primary-text-hover)]'
          >
            <ClipboardDocumentIcon className='h-4 w-4' />
            Copy
          </button>
        </div>

        <pre className='overflow-x-auto rounded-lg border border-white/5 bg-[#0d1117] px-4 py-4 text-xs leading-6 text-[#c9d1d9]'>
          <code>{configText}</code>
        </pre>

        <div className='mt-4 rounded-lg border border-[rgba(124,58,237,0.15)] bg-[rgba(124,58,237,0.06)] p-3'>
          <div className='mb-1 flex items-center gap-2 text-xs font-semibold text-[var(--jarvis-primary-text)]'>
            <InformationCircleIcon className='h-4 w-4' />
            Authentication
          </div>
          <p className='text-xs leading-5 text-[var(--jarvis-muted)]'>
            Authentication is handled automatically by your identity provider.
          </p>
        </div>
      </div>
    </div>
  );
};

export default ServerConfigModal;
