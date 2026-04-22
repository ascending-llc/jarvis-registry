import { Dialog, Transition } from '@headlessui/react';
import { ArrowDownTrayIcon, CheckIcon, ClipboardIcon } from '@heroicons/react/24/outline';
import { Fragment, useState } from 'react';

type TokenModalProps = {
  tokenData: any;
  showTokenModal: boolean;
  setShowTokenModal: (show: boolean) => void;
};

const TokenModal: React.FC<TokenModalProps> = ({ tokenData, showTokenModal, setShowTokenModal }) => {
  const [copied, setCopied] = useState(false);

  /** Handle copying token data to clipboard */
  const handleCopyTokens = async () => {
    if (!tokenData) return;

    const formattedData = JSON.stringify(tokenData, null, 2);
    try {
      await navigator.clipboard.writeText(formattedData);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error('Failed to copy:', error);
    }
  };

  /** Handle downloading token data as a JSON file */
  const handleDownloadTokens = () => {
    if (!tokenData) return;

    const formattedData = JSON.stringify(tokenData, null, 2);
    const blob = new Blob([formattedData], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `mcp-registry-api-tokens-${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <Transition appear show={showTokenModal} as={Fragment}>
      <Dialog as='div' className="relative z-50" onClose={() => setShowTokenModal(false)}>
        <Transition.Child
          as={Fragment}
          enter='ease-out duration-300'
          enterFrom='opacity-0'
          enterTo='opacity-100'
          leave='ease-in duration-200'
          leaveFrom='opacity-100'
          leaveTo='opacity-0'
        >
          <div className="fixed inset-0 bg-black bg-opacity-25" />
        </Transition.Child>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4 text-center">
            <Transition.Child
              as={Fragment}
              enter='ease-out duration-300'
              enterFrom='opacity-0 scale-95'
              enterTo='opacity-100 scale-100'
              leave='ease-in duration-200'
              leaveFrom='opacity-100 scale-100'
              leaveTo='opacity-0 scale-95'
            >
              <Dialog.Panel className="w-full max-w-3xl transform overflow-hidden rounded-2xl bg-[var(--jarvis-card)] bg-[var(--jarvis-card)] p-6 text-left align-middle shadow-xl transition-all">
                <Dialog.Title as='h3' className="text-lg font-medium leading-6 text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)] mb-4">
                  Keycloak Admin Tokens
                </Dialog.Title>

                {tokenData && (
                  <div className="space-y-4">
                    {/* Action Buttons */}
                    <div className="flex space-x-2">
                      <button
                        onClick={handleCopyTokens}
                        className="flex items-center space-x-2 px-4 py-2 bg-[var(--jarvis-info-text)] text-white rounded-lg hover:bg-[var(--jarvis-info-text)] transition-colors text-sm"
                      >
                        {copied ? (
                          <>
                            <CheckIcon className="h-4 w-4" />
                            <span>Copied!</span>
                          </>
                        ) : (
                          <>
                            <ClipboardIcon className="h-4 w-4" />
                            <span>Copy JSON</span>
                          </>
                        )}
                      </button>
                      <button
                        onClick={handleDownloadTokens}
                        className="flex items-center space-x-2 px-4 py-2 bg-[var(--jarvis-success)] text-white rounded-lg hover:bg-[var(--jarvis-success)] transition-colors text-sm"
                      >
                        <ArrowDownTrayIcon className="h-4 w-4" />
                        <span>Download JSON</span>
                      </button>
                    </div>

                    {/* Token Data Display */}
                    <div className="bg-[var(--jarvis-bg)] bg-[var(--jarvis-card)] rounded-lg p-4 max-h-96 overflow-y-auto">
                      <pre className="text-xs text-[var(--jarvis-text)] text-[var(--jarvis-text)] whitespace-pre-wrap break-all">
                        {JSON.stringify(tokenData, null, 2)}
                      </pre>
                    </div>

                    {/* Close Button */}
                    <div className="flex justify-end">
                      <button
                        onClick={() => setShowTokenModal(false)}
                        className="px-4 py-2 bg-[var(--jarvis-card-muted)] bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)] text-[var(--jarvis-text)] rounded-lg hover:bg-[var(--jarvis-card-muted)] hover:bg-[var(--jarvis-card-muted)] transition-colors text-sm"
                      >
                        Close
                      </button>
                    </div>
                  </div>
                )}
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition>
  );
};

export default TokenModal;
