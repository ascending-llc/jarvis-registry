import { Dialog } from '@headlessui/react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useState } from 'react';
import { getBasePath } from '@/config';

interface ServerCreationSuccessDialogProps {
  isOpen: boolean;
  serverData: { serverName: string; path: string };
  onClose: () => void;
}

const ServerCreationSuccessDialog: React.FC<ServerCreationSuccessDialogProps> = ({ isOpen, serverData, onClose }) => {
  const [copied, setCopied] = useState(false);
  // Construct redirect URI using server path (ensure path starts with /)
  const cleanPath = serverData.path?.replace(/\/+$/, '') || '';
  const serverPath = cleanPath && !cleanPath.startsWith('/') ? `/${cleanPath}` : cleanPath;
  const redirectUri = `${window.location.protocol}//${window.location.host}${getBasePath()}/api/v1/mcp${serverPath}/oauth/callback`;

  const handleCopy = () => {
    navigator.clipboard.writeText(redirectUri);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!isOpen) return null;

  return (
    <Dialog as='div' className='relative z-50' open={isOpen} onClose={() => {}}>
      <div className='fixed inset-0 bg-black/50' aria-hidden='true' />

      <div className='fixed inset-0 flex items-center justify-center p-4'>
        <Dialog.Panel className='w-full max-w-lg overflow-hidden rounded-xl bg-[var(--jarvis-card)] shadow-xl'>
          {/* Header */}
          <div className='flex items-center justify-between px-6 py-4'>
            <Dialog.Title className='text-lg font-bold text-[var(--jarvis-text-strong)]'>
              MCP server created successfully
            </Dialog.Title>
            <button
              onClick={onClose}
              className='text-[var(--jarvis-icon)] hover:text-[var(--jarvis-icon-hover)]'
            >
              <XMarkIcon className='h-6 w-6' />
            </button>
          </div>

          <div className='border-b border-[color:var(--jarvis-border)]' />

          {/* Content */}
          <div className='px-6 py-6'>
            <p className='mb-4 text-sm text-[var(--jarvis-muted)]'>
              Copy this redirect URI and configure it in your OAuth provider settings.
            </p>

            <div className='space-y-2 rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-4'>
              <label className='block text-xs font-semibold uppercase tracking-wider text-[var(--jarvis-text)]'>
                Redirect URI
              </label>
              <div className='flex items-center space-x-2'>
                <div className='flex-1 relative'>
                  <input
                    type='text'
                    readOnly
                    value={redirectUri}
                    className='w-full rounded-md border border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] px-3 py-2 text-sm text-[var(--jarvis-text)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)]'
                  />
                </div>
                <button
                  onClick={handleCopy}
                  className='flex min-w-[100px] items-center justify-center rounded-md border border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] px-4 py-2 text-sm font-medium text-[var(--jarvis-text)] hover:bg-[var(--jarvis-primary-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-2'
                >
                  {copied ? 'Copied!' : 'Copy link'}
                </button>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div className='flex justify-end border-t border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-6 py-4'>
            <button
              onClick={onClose}
              className='rounded-md border border-transparent bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-[var(--jarvis-primary-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-2'
            >
              Done
            </button>
          </div>
        </Dialog.Panel>
      </div>
    </Dialog>
  );
};

export default ServerCreationSuccessDialog;
