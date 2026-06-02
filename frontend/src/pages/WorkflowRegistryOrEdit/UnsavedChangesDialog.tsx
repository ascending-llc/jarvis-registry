import { Dialog, Transition } from '@headlessui/react';
import { ExclamationTriangleIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment } from 'react';

interface UnsavedChangesDialogProps {
  isOpen: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

const UnsavedChangesDialog: React.FC<UnsavedChangesDialogProps> = ({ isOpen, onCancel, onConfirm }) => {
  return (
    <Transition appear show={isOpen} as={Fragment}>
      <Dialog as='div' className='relative z-50' onClose={onCancel}>
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
              <Dialog.Panel className='w-full max-w-md transform overflow-hidden rounded-xl bg-[var(--jarvis-card)] p-6 shadow-xl transition-all'>
                <div className='flex items-center gap-3 mb-4'>
                  <div className='flex h-10 w-10 items-center justify-center rounded-full bg-[var(--jarvis-warning-soft)]'>
                    <ExclamationTriangleIcon className='h-5 w-5 text-[var(--jarvis-warning-text)]' />
                  </div>
                  <Dialog.Title as='h3' className='text-lg font-semibold text-[var(--jarvis-text-strong)]'>
                    Unsaved Changes
                  </Dialog.Title>
                </div>

                <p className='text-sm text-[var(--jarvis-text)] mb-6'>
                  Your edits have not been saved. Leaving now will lose your progress. Are you sure you want to leave?
                </p>

                <div className='flex justify-end gap-3'>
                  <button
                    onClick={onCancel}
                    className='px-4 py-2 rounded-lg text-sm font-medium bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)] hover:bg-[var(--jarvis-surface)] transition-colors'
                  >
                    Cancel
                  </button>
                  <button
                    onClick={onConfirm}
                    className='px-4 py-2 rounded-lg text-sm font-medium bg-[var(--jarvis-danger)] text-white hover:opacity-90 transition-colors'
                  >
                    Leave
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

export default UnsavedChangesDialog;
