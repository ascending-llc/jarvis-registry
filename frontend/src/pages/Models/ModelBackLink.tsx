import { ArrowLeftIcon } from '@heroicons/react/24/outline';
import type React from 'react';

type ModelBackLinkProps = {
  label: string;
  onBack: () => void;
  disabled?: boolean;
};

const ModelBackLink: React.FC<ModelBackLinkProps> = ({ label, onBack, disabled = false }) => (
  <div className='mb-5 flex flex-shrink-0 items-center gap-2'>
    <button
      type='button'
      aria-label={`Back to ${label}`}
      onClick={onBack}
      disabled={disabled}
      className='inline-flex h-7 w-7 items-center justify-center rounded-[7px] text-[var(--jarvis-muted)] transition hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-50'
    >
      <ArrowLeftIcon className='h-[15px] w-[15px]' />
    </button>
    <button
      type='button'
      onClick={onBack}
      disabled={disabled}
      className='text-[13.5px] text-[var(--jarvis-muted)] transition hover:text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-50'
    >
      {label}
    </button>
  </div>
);

export default ModelBackLink;
