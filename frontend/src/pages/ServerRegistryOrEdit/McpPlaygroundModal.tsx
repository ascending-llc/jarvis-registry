import { XMarkIcon } from '@heroicons/react/24/outline';
import { JarvisEmbed } from 'jarvis-embed';
import { useEffect, useRef, useState } from 'react';

import HELPER from '@/helper';
import SERVICES from '@/services';

const JARVIS_URL = HELPER.getJarvisUrl();

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
          width: '100%',
          height: '100%',
          onError: () => setError('Failed to connect to Jarvis'),
        });
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
      <div className='flex flex-col w-[480px] h-[620px] bg-white dark:bg-gray-800 rounded-xl shadow-2xl overflow-hidden'>
        <div className='flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-gray-700'>
          <div>
            <p className='text-sm font-semibold text-gray-900 dark:text-white'>Playground</p>
            <p className='text-xs text-gray-400 font-mono'>{serverName}</p>
          </div>
          <button
            onClick={onClose}
            className='p-1 rounded-md text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700'
          >
            <XMarkIcon className='h-5 w-5' />
          </button>
        </div>

        <div className='flex-1 overflow-hidden'>
          {error ? (
            <div className='h-full flex items-center justify-center text-sm text-red-500'>{error}</div>
          ) : (
            <div ref={setContainer} className='w-full h-full' />
          )}
        </div>
      </div>
    </div>
  );
};

export default McpPlaygroundModal;
