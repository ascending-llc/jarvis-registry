import type React from 'react';
import { useEffect, useId } from 'react';
import { createPortal } from 'react-dom';

interface RejectFeedbackDialogProps {
  isOpen: boolean;
  feedback: string;
  submitting: boolean;
  onFeedbackChange: (feedback: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}

export const RejectFeedbackDialog: React.FC<RejectFeedbackDialogProps> = ({
  isOpen,
  feedback,
  submitting,
  onFeedbackChange,
  onCancel,
  onConfirm,
}) => {
  const titleId = useId();
  const feedbackId = useId();

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !submitting) onCancel();
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onCancel, submitting]);

  if (!isOpen) return null;

  return createPortal(
    <div
      className='fixed inset-0 z-[120] flex items-center justify-center px-4'
      role='dialog'
      aria-modal='true'
      aria-labelledby={titleId}
    >
      <button
        type='button'
        aria-label='Close reject dialog'
        className='fixed inset-0 cursor-default bg-black/50'
        onClick={onCancel}
        disabled={submitting}
      />
      <div className='relative w-full max-w-md rounded-lg border border-[var(--jarvis-border)] bg-[var(--jarvis-card)] p-5 shadow-xl'>
        <h3 id={titleId} className='mb-2 text-sm font-semibold text-[var(--jarvis-text-strong)]'>
          Reject and retry
        </h3>
        <p className='mb-3 text-xs text-[var(--jarvis-muted)]'>
          Optionally explain what should change before this step is retried.
        </p>
        <label className='mb-1 block text-xs text-[var(--jarvis-muted)]' htmlFor={feedbackId}>
          Feedback
        </label>
        <textarea
          id={feedbackId}
          value={feedback}
          onChange={event => onFeedbackChange(event.target.value)}
          disabled={submitting}
          className='h-24 w-full resize-none rounded-md border border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-3 py-2 text-xs text-[var(--jarvis-text-strong)] outline-none focus:border-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-50'
          placeholder='Describe what should be corrected on retry...'
        />
        <div className='mt-4 flex justify-end gap-2'>
          <button
            type='button'
            onClick={onCancel}
            disabled={submitting}
            className='rounded-md border border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-3 py-1.5 text-xs text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-50'
          >
            Cancel
          </button>
          <button
            type='button'
            onClick={onConfirm}
            disabled={submitting}
            className='rounded-md bg-[var(--jarvis-danger)] px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50'
          >
            {submitting ? 'Rejecting...' : 'Confirm reject'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
};
