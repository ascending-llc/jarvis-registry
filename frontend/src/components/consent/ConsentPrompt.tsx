import { CheckCircleIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';

interface ConsentPromptProps {
  clientName: string;
  clientUri: string | null;
  ipAddress: string | null;
  registeredAt: number | null;
  description: string;
  onApprove: () => void;
  approving: boolean;
}

const ConsentPrompt: React.FC<ConsentPromptProps> & {
  Loading: React.FC;
  Error: React.FC<{ message: string; details?: string }>;
  Success: React.FC<{ message: string; submessage?: string }>;
} = ({ clientName, clientUri, ipAddress, registeredAt, description, onApprove, approving }) => {
  const registeredLabel = registeredAt ? new Date(registeredAt * 1000).toLocaleString() : null;

  return (
    <div className='card p-10 max-w-md w-full text-center animate-slide-up'>
      <h1 className='text-xl font-semibold text-[var(--jarvis-text-strong)] mb-4'>
        <span className='text-[var(--jarvis-primary)]'>{clientName}</span> wants to access your account
      </h1>
      {clientUri && <p className='text-sm text-[var(--jarvis-muted)] break-all mb-1'>{clientUri}</p>}
      {registeredLabel && (
        <p className='text-xs text-[var(--jarvis-muted)] mb-4'>
          Registered {registeredLabel}
          {ipAddress ? ` from ${ipAddress}` : ''}
        </p>
      )}
      <p className='text-base text-[var(--jarvis-text)] mb-6'>{description}</p>
      <button
        type='button'
        onClick={onApprove}
        disabled={approving}
        className='btn-primary w-full shadow-md hover:shadow-lg transition-all duration-200'
      >
        {approving ? 'Authorizing…' : 'Authorize'}
      </button>
      <p className='text-xs text-[var(--jarvis-muted)] mt-6'>Only authorize applications you recognize and trust.</p>
    </div>
  );
};

ConsentPrompt.Loading = () => (
  <div className='card p-10 max-w-md w-full text-center animate-slide-up'>
    <div className='flex flex-col items-center justify-center space-y-4'>
      <div className='w-10 h-10 border-4 border-[var(--jarvis-primary-soft)] border-t-[var(--jarvis-primary)] rounded-full animate-spin'></div>
      <p className='text-[var(--jarvis-muted)]'>Loading authorization details...</p>
    </div>
  </div>
);

ConsentPrompt.Error = ({ message, details }) => (
  <div className='card p-10 max-w-md w-full text-center animate-slide-up'>
    <div className='mx-auto mb-8 w-16 h-16 bg-[var(--jarvis-danger)] dark:bg-[var(--jarvis-danger)] rounded-full flex items-center justify-center animate-pulse'>
      <XMarkIcon className='w-10 h-10 text-white' strokeWidth={3} />
    </div>
    <h1 className='text-xl font-semibold text-[var(--jarvis-text-strong)] mb-4'>Authorization Failed</h1>
    <p className='text-base text-[var(--jarvis-text)] mb-6 leading-relaxed'>{message}</p>
    {details && (
      <div className='bg-[var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)] p-4 rounded-lg text-sm mb-6 font-mono break-words text-left'>
        <strong className='block mb-2'>Error Details:</strong>
        {details}
      </div>
    )}
  </div>
);

ConsentPrompt.Success = ({ message, submessage }) => (
  <div className='card p-10 max-w-md w-full text-center animate-slide-up'>
    <div className='mx-auto mb-8 w-16 h-16 bg-[var(--jarvis-success)] dark:bg-[var(--jarvis-primary)] rounded-full flex items-center justify-center animate-pulse'>
      <CheckCircleIcon className='w-10 h-10 text-white' />
    </div>
    <h1 className='text-2xl font-semibold text-[var(--jarvis-text-strong)] mb-6'>Authorization Successful</h1>
    <p className='text-base text-[var(--jarvis-text)] mb-4 leading-relaxed'>{message}</p>
    {submessage && <p className='text-sm text-[var(--jarvis-muted)]'>{submessage}</p>}
  </div>
);

export default ConsentPrompt;
