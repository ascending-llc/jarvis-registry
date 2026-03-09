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
        <Dialog.Panel className='w-full max-w-lg bg-white dark:bg-gray-800 rounded-xl shadow-xl overflow-hidden'>
          {/* Header */}
          <div className='px-6 py-4 flex items-center justify-between'>
            <Dialog.Title className='text-lg font-bold text-gray-900 dark:text-white'>
              MCP server created successfully
            </Dialog.Title>
            <button
              onClick={onClose}
              className='text-gray-400 hover:text-gray-500 dark:text-gray-400 dark:hover:text-gray-300'
            >
              <XMarkIcon className='h-6 w-6' />
            </button>
          </div>

          <div className='border-b border-gray-100 dark:border-gray-700' />

          {/* Content */}
          <div className='px-6 py-6'>
            <p className='text-sm text-gray-500 dark:text-gray-400 mb-4'>
              Copy this redirect URI and configure it in your OAuth provider settings.
            </p>

            <div className='bg-gray-50 dark:bg-gray-700/50 p-4 rounded-lg border border-gray-200 dark:border-gray-600 space-y-2'>
              <label className='block text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider'>
                Redirect URI
              </label>
              <div className='flex items-center space-x-2'>
                <div className='flex-1 relative'>
                  <input
                    type='text'
                    readOnly
                    value={redirectUri}
                    className='w-full px-3 py-2 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-purple-500'
                  />
                </div>
                <button
                  onClick={handleCopy}
                  className='flex items-center px-4 py-2 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md text-sm font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 min-w-[100px] justify-center'
                >
                  {copied ? 'Copied!' : 'Copy link'}
                </button>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div className='px-6 py-4 flex justify-end bg-gray-50 dark:bg-gray-800/50 border-t border-gray-100 dark:border-gray-700'>
            <button
              onClick={onClose}
              className='px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-purple-700 hover:bg-green-800 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500'
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
