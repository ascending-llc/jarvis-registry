import type React from 'react';

interface DeleteWorkflowDialogProps {
  isOpen: boolean;
  workflowName: string;
  deleting?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

const DeleteWorkflowDialog: React.FC<DeleteWorkflowDialogProps> = ({
  isOpen,
  workflowName,
  deleting = false,
  onCancel,
  onConfirm,
}) => {
  if (!isOpen) return null;

  return (
    <div className='fixed inset-0 z-50 flex items-center justify-center'>
      <div className='fixed inset-0 bg-black/50' onClick={deleting ? undefined : onCancel} />
      <div className='relative bg-[var(--jarvis-card)] border border-[var(--jarvis-border)] rounded-lg shadow-xl max-w-sm w-full mx-4 p-5'>
        <h3 className='text-sm font-semibold text-[var(--jarvis-text-strong)] mb-3'>Delete workflow</h3>
        <p className='text-xs text-[var(--jarvis-text)] mb-5'>
          Are you sure you want to delete <span className='font-medium'>{workflowName}</span>? This action cannot be
          undone.
        </p>
        <div className='flex justify-end gap-2'>
          <button
            type='button'
            onClick={onCancel}
            disabled={deleting}
            className='px-3 py-1.5 text-xs font-medium text-[var(--jarvis-text)] bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md hover:bg-[var(--jarvis-surface)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
          >
            Cancel
          </button>
          <button
            type='button'
            onClick={onConfirm}
            disabled={deleting}
            className='px-3 py-1.5 text-xs font-medium text-white bg-[var(--jarvis-danger)] border border-transparent rounded-md hover:opacity-90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5'
          >
            {deleting && (
              <svg className='animate-spin h-3 w-3' viewBox='0 0 24 24' fill='none'>
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

export default DeleteWorkflowDialog;
