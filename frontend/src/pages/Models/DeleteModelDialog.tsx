import { ArrowPathIcon } from '@heroicons/react/24/outline';
import type React from 'react';

type DeleteModelDialogProps = {
  isOpen: boolean;
  modelName: string;
  deleting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

const DeleteModelDialog: React.FC<DeleteModelDialogProps> = ({ isOpen, modelName, deleting, onCancel, onConfirm }) => {
  if (!isOpen) return null;

  return (
    <div className='fixed inset-0 z-50 flex items-center justify-center'>
      <div className='fixed inset-0 bg-black/50' onClick={deleting ? undefined : onCancel} />
      <div
        role='dialog'
        aria-modal='true'
        aria-labelledby='delete-model-title'
        className='relative mx-4 w-full max-w-sm rounded-lg border border-[var(--jarvis-border)] bg-[var(--jarvis-card)] p-5 shadow-xl'
      >
        <h3 id='delete-model-title' className='mb-3 text-sm font-semibold text-[var(--jarvis-text-strong)]'>
          Delete model
        </h3>
        <p className='mb-3 text-xs text-[var(--jarvis-text)]'>
          Are you sure you want to delete <span className='font-medium'>{modelName}</span>? This action cannot be
          undone.
        </p>
        <p className='mb-5 text-xs text-[var(--jarvis-muted)]'>
          Models selected as a gateway default or used by workflows cannot be deleted.
        </p>
        <div className='flex justify-end gap-2'>
          <button
            type='button'
            onClick={onCancel}
            disabled={deleting}
            className='rounded-md border border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-3 py-1.5 text-xs font-medium text-[var(--jarvis-text)] transition-colors hover:bg-[var(--jarvis-surface)] disabled:cursor-not-allowed disabled:opacity-50'
          >
            Cancel
          </button>
          <button
            type='button'
            onClick={onConfirm}
            disabled={deleting}
            className='inline-flex items-center gap-1.5 rounded-md border border-transparent bg-[var(--jarvis-danger)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50'
          >
            {deleting && <ArrowPathIcon className='h-3 w-3 animate-spin' />}
            Delete
          </button>
        </div>
      </div>
    </div>
  );
};

export default DeleteModelDialog;
