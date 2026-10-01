import { Dialog, Transition } from '@headlessui/react';
import { ArrowTopRightOnSquareIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment } from 'react';
import { createPortal } from 'react-dom';

import { GITHUB_AUTHORIZATION_PROMPT } from '@/services/externalProvider/githubAuthorization';

interface GithubAuthorizationDialogProps {
  isOpen: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

const GithubAuthorizationDialog: React.FC<GithubAuthorizationDialogProps> = ({ isOpen, onCancel, onConfirm }) =>
  createPortal(
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
                <div className='mb-4 flex items-center gap-3'>
                  <div className='flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--jarvis-primary-soft)]'>
                    <ArrowTopRightOnSquareIcon className='h-5 w-5 text-[var(--jarvis-primary)]' />
                  </div>
                  <Dialog.Title as='h3' className='text-lg font-semibold text-[var(--jarvis-text-strong)]'>
                    Authorize with GitHub
                  </Dialog.Title>
                </div>

                <Dialog.Description className='mb-6 text-sm text-[var(--jarvis-text)]'>
                  {GITHUB_AUTHORIZATION_PROMPT}
                </Dialog.Description>

                <div className='flex flex-col-reverse justify-end gap-2 sm:flex-row'>
                  <button
                    type='button'
                    onClick={onCancel}
                    className='rounded-lg bg-[var(--jarvis-card-muted)] px-4 py-2 text-sm font-medium text-[var(--jarvis-text)] transition-colors hover:bg-[var(--jarvis-surface)]'
                  >
                    Cancel
                  </button>
                  <button
                    type='button'
                    onClick={onConfirm}
                    className='inline-flex items-center justify-center gap-2 rounded-lg bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)]'
                  >
                    Continue to GitHub
                    <ArrowTopRightOnSquareIcon className='h-4 w-4' />
                  </button>
                </div>
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition>,
    document.body,
  );

export default GithubAuthorizationDialog;
