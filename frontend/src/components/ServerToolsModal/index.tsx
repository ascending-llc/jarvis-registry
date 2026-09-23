import { Dialog, Transition } from '@headlessui/react';
import { WrenchScrewdriverIcon, XMarkIcon } from '@heroicons/react/24/outline';
import { Fragment } from 'react';
import { CheckboxField } from '@/components/FormFields/CheckboxField';
import IconButton from '@/components/IconButton';
import { type ServerToolsModalProps, useServerToolsModal } from './useServerToolsModal';

const ServerToolsModal: React.FC<ServerToolsModalProps> = props => {
  const { isOpen, serverId, serverName, canManageTools } = props;
  const { tools, disabledTools, loading, loaded, saving, hasChanges, toggleTool, handleSave, requestClose } =
    useServerToolsModal(props);

  return (
    <Transition.Root show={isOpen} as={Fragment}>
      <Dialog as='div' className='relative z-50' onClose={requestClose}>
        <Transition.Child
          as={Fragment}
          enter='ease-out duration-300'
          enterFrom='opacity-0'
          enterTo='opacity-100'
          leave='ease-in duration-200'
          leaveFrom='opacity-100'
          leaveTo='opacity-0'
        >
          <div className='fixed inset-0 bg-black/50 backdrop-blur-sm' />
        </Transition.Child>

        <div className='fixed inset-0 z-10 flex items-center justify-center'>
          <Transition.Child
            as={Fragment}
            enter='ease-out duration-300'
            enterFrom='opacity-0 scale-95'
            enterTo='opacity-100 scale-100'
            leave='ease-in duration-200'
            leaveFrom='opacity-100 scale-100'
            leaveTo='opacity-0 scale-95'
          >
            <Dialog.Panel className='mx-4 flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl bg-[var(--jarvis-card)] p-6 shadow-xl'>
              <div className='mb-6 flex items-center justify-between gap-4'>
                <div className='flex min-w-0 items-center gap-3'>
                  <WrenchScrewdriverIcon className='h-6 w-6 shrink-0 text-[var(--jarvis-text-strong)]' />
                  <Dialog.Title as='h2' className='truncate text-xl font-semibold text-[var(--jarvis-text-strong)]'>
                    Tools for {serverName}
                  </Dialog.Title>
                </div>
                <IconButton
                  ariaLabel='Close'
                  tooltip='Close'
                  onClick={requestClose}
                  disabled={saving}
                  size='card'
                  className='border-none bg-transparent text-[var(--jarvis-muted)] shadow-none hover:bg-transparent hover:text-[var(--jarvis-icon-hover)]'
                >
                  <XMarkIcon className='h-6 w-6' />
                </IconButton>
              </div>

              <div className='min-h-0 flex-1 overflow-y-auto pr-1'>
                {loading ? (
                  <div className='flex items-center justify-center p-8'>
                    <div className='h-6 w-6 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]' />
                  </div>
                ) : !loaded ? (
                  <div className='rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-6 text-center text-sm text-[var(--jarvis-muted)]'>
                    Unable to load tools. Close and reopen the dialog to try again.
                  </div>
                ) : tools.length === 0 ? (
                  <p className='py-6 text-center text-sm text-[var(--jarvis-muted)]'>
                    No tools available for this server.
                  </p>
                ) : (
                  <div className='space-y-4'>
                    {tools.map(tool => {
                      const enabled = !disabledTools.has(tool.mcpToolName);
                      return (
                        <div
                          key={tool.mcpToolName}
                          className='rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-4'
                        >
                          <div className='flex items-start justify-between gap-4'>
                            <h3 className='min-w-0 break-words font-medium text-[var(--jarvis-text-strong)]'>
                              {tool.function.name}
                            </h3>
                            {canManageTools ? (
                              <CheckboxField
                                id={`tool-${encodeURIComponent(serverId)}-${encodeURIComponent(tool.mcpToolName)}`}
                                label='Enabled'
                                checked={enabled}
                                disabled={saving}
                                onChange={() => toggleTool(tool.mcpToolName)}
                                className='shrink-0'
                              />
                            ) : (
                              <div className='ml-auto flex shrink-0 items-center gap-1.5 whitespace-nowrap text-xs text-[var(--jarvis-subtle)]'>
                                <span
                                  className={`h-1.5 w-1.5 rounded-full ${enabled ? 'bg-[var(--jarvis-success)]' : 'bg-[var(--jarvis-subtle)]'}`}
                                />
                                {enabled ? 'Enabled' : 'Disabled'}
                              </div>
                            )}
                          </div>

                          {tool.function.description && (
                            <p className='mt-2 text-sm text-[var(--jarvis-muted)]'>{tool.function.description}</p>
                          )}
                          {tool.function.parameters && (
                            <details className='mt-2 text-xs'>
                              <summary className='cursor-pointer text-[var(--jarvis-muted)]'>View Schema</summary>
                              <pre className='mt-2 max-h-64 overflow-auto rounded border border-[color:var(--jarvis-border)] bg-[var(--jarvis-surface)] p-3 text-[var(--jarvis-text)]'>
                                {JSON.stringify(tool.function.parameters, null, 2)}
                              </pre>
                            </details>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {canManageTools && (
                <div className='mt-6 flex items-center justify-end gap-3 border-t border-[color:var(--jarvis-border)] pt-4'>
                  <button
                    type='button'
                    onClick={requestClose}
                    disabled={saving}
                    className='rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-4 py-2 text-sm font-semibold text-[var(--jarvis-text)] transition-colors hover:bg-[var(--jarvis-card-muted)] disabled:cursor-not-allowed disabled:opacity-50'
                  >
                    {hasChanges ? 'Cancel' : 'Close'}
                  </button>
                  <button
                    type='button'
                    onClick={handleSave}
                    disabled={saving || loading || !loaded || !hasChanges}
                    className='inline-flex items-center gap-2 rounded-lg bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-50'
                  >
                    {saving && <div className='h-4 w-4 animate-spin rounded-full border-b-2 border-white' />}
                    Save Changes
                  </button>
                </div>
              )}
            </Dialog.Panel>
          </Transition.Child>
        </div>
      </Dialog>
    </Transition.Root>
  );
};

export default ServerToolsModal;
