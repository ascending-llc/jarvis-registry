import { Dialog, Transition } from '@headlessui/react';
import { ExclamationTriangleIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment } from 'react';

interface TriggerUnsavedChangesDialogProps {
  isOpen: boolean;
  saving: boolean;
  onCancel: () => void;
  onContinueWithoutSaving: () => void;
  onSaveAndContinue: () => Promise<void>;
}

const TriggerUnsavedChangesDialog: React.FC<TriggerUnsavedChangesDialogProps> = ({
  isOpen,
  saving,
  onCancel,
  onContinueWithoutSaving,
  onSaveAndContinue,
}) => {
  const handleClose = () => {
    if (!saving) onCancel();
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
          <div className='fixed inset-0 bg-black/25' />
        </Transition.Child>

        <div className='fixed inset-0 overflow-y-auto'>
          <div className='flex min-h-full items-center justify-center p-4'>
            <Transition.Child
              as={Fragment}
              enter='ease-out duration-200'
              enterFrom='opacity-0 scale-95'
              enterTo='opacity-100 scale-100'
              leave='ease-in duration-150'
              leaveFrom='opacity-100 scale-100'
              leaveTo='opacity-0 scale-95'
            >
              <Dialog.Panel className='w-full max-w-lg transform overflow-hidden rounded-xl bg-[var(--jarvis-card)] p-6 shadow-xl transition-all'>
                <div className='mb-4 flex items-center gap-3'>
                  <div className='flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full bg-[var(--jarvis-warning-soft)]'>
                    <ExclamationTriangleIcon className='h-5 w-5 text-[var(--jarvis-warning-text)]' />
                  </div>
                  <Dialog.Title as='h3' className='text-lg font-semibold text-[var(--jarvis-text-strong)]'>
                    Unsaved changes
                  </Dialog.Title>
                </div>

                <Dialog.Description className='mb-6 text-sm text-[var(--jarvis-text)]'>
                  This workflow has unsaved edits. Continuing without saving will run the last saved version, not the
                  changes currently on screen.
                </Dialog.Description>

                <div className='flex flex-col-reverse justify-end gap-2 sm:flex-row'>
                  <button
                    type='button'
                    onClick={onCancel}
                    disabled={saving}
                    className='rounded-lg bg-[var(--jarvis-card-muted)] px-4 py-2 text-sm font-medium text-[var(--jarvis-text)] transition-colors hover:bg-[var(--jarvis-surface)] disabled:cursor-not-allowed disabled:opacity-50'
                  >
                    Cancel
                  </button>
                  <button
                    type='button'
                    onClick={onContinueWithoutSaving}
                    disabled={saving}
                    className='rounded-lg border border-[var(--jarvis-border)] bg-transparent px-4 py-2 text-sm font-medium text-[var(--jarvis-text)] transition-colors hover:bg-[var(--jarvis-card-muted)] disabled:cursor-not-allowed disabled:opacity-50'
                  >
                    Continue without saving
                  </button>
                  <button
                    type='button'
                    onClick={() => void onSaveAndContinue()}
                    disabled={saving}
                    className='inline-flex items-center justify-center gap-2 rounded-lg bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50'
                  >
                    {saving && (
                      <span className='h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-b-white' />
                    )}
                    Save and continue
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

export default TriggerUnsavedChangesDialog;
