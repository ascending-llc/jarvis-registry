import { JarvisEmbed } from '@ascending-inc/jarvis-embed';
import { XMarkIcon } from '@heroicons/react/24/outline';
import { useEffect, useRef, useState } from 'react';

import SERVICES from '@/services';
import UTILS from '@/utils';

const JARVIS_URL = UTILS.getJarvisUrl();

type Props = {
  serverName: string;
  onClose: () => void;
};

const McpPlaygroundModal = ({ serverName, onClose }: Props) => {
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const jarvisRef = useRef<JarvisEmbed | null>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (!container) return;

    let destroyed = false;

    SERVICES.AUTH.getToken({ expiresInHours: 1, description: 'Jarvis MCP test' })
      .then(result => {
        if (destroyed) return;

        const token = result.tokenData?.accessToken;
        if (!token) {
          setError('Failed to retrieve access token');
          return;
        }

        const embed = new JarvisEmbed({
          provider: 'direct',
          token,
          model: 'anthropic-claude-sonnet-4-6',
          apiUrl: JARVIS_URL,
          container,
          artifactsButton: true,
          width: '100%',
          height: '100%',
          onError: () => setError('Failed to connect to Jarvis'),
        } as any);
        embed.setMcpServers([serverName]);
        jarvisRef.current = embed;
      })
      .catch(() => {
        if (!destroyed) {
          setError('Failed to authenticate with Jarvis');
        }
      });

    return () => {
      destroyed = true;
      jarvisRef.current?.destroy();
      jarvisRef.current = null;
    };
  }, [container, serverName]);

  return (
    <div className='fixed inset-0 z-50 flex items-center justify-center bg-black/50'>
      <div className='flex flex-col w-[480px] h-[620px] bg-[var(--jarvis-card)] rounded-xl shadow-2xl overflow-hidden'>
        <div className='flex items-center justify-between px-4 py-3 border-b border-[color:var(--jarvis-border-soft)] border-[color:var(--jarvis-border)]'>
          <div>
            <p className='text-sm font-semibold text-[var(--jarvis-text-strong)]'>Playground</p>
            <p className='text-xs text-[var(--jarvis-subtle)] font-mono'>{serverName}</p>
          </div>
          <button
            onClick={onClose}
            className='p-1 rounded-md text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-muted)] hover:text-[var(--jarvis-icon-hover)] hover:bg-[var(--jarvis-card-muted)] hover:bg-[var(--jarvis-card-muted)]'
          >
            <XMarkIcon className='h-5 w-5' />
          </button>
        </div>

        <div className='flex-1 overflow-hidden'>
          {error ? (
            <div className='h-full flex items-center justify-center text-sm text-[var(--jarvis-danger-text)]'>
              {error}
            </div>
          ) : (
            <div ref={setContainer} className='w-full h-full' />
          )}
        </div>
      </div>
    </div>
  );
};

export default McpPlaygroundModal;
