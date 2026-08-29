import type React from 'react';

type DeleteSkillDialogProps = {
  isOpen: boolean;
  skillName: string;
  deleting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

const DeleteSkillDialog: React.FC<DeleteSkillDialogProps> = ({ isOpen, skillName, deleting, onCancel, onConfirm }) => {
  if (!isOpen) return null;

  return (
    <div className='fixed inset-0 z-50 flex items-center justify-center'>
      <div className='fixed inset-0 bg-black/50' onClick={deleting ? undefined : onCancel} />
      <div
        role='dialog'
        aria-modal='true'
        aria-labelledby='delete-skill-title'
        className='relative mx-4 w-full max-w-sm rounded-lg border border-[var(--jarvis-border)] bg-[var(--jarvis-card)] p-5 shadow-xl'
      >
        <h3 id='delete-skill-title' className='mb-3 text-sm font-semibold text-[var(--jarvis-text-strong)]'>
          Delete skill
        </h3>
        <p className='mb-5 text-xs text-[var(--jarvis-text)]'>
          Are you sure you want to delete <span className='font-medium'>{skillName}</span>? This action cannot be
          undone.
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
            {deleting && (
              <svg className='h-3 w-3 animate-spin' viewBox='0 0 24 24' fill='none' aria-hidden='true'>
                <circle className='opacity-25' cx='12' cy='12' r='10' stroke='currentColor' strokeWidth='4' />
                <path
                  className='opacity-75'
                  fill='currentColor'
                  d='M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z'
                />
              </svg>
            )}
            Delete
          </button>
        </div>
      </div>
    </div>
  );
};

export default DeleteSkillDialog;
