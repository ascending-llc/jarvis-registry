import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { AuthPageLayout } from '@/components/auth/AuthPageLayout';
import ConsentPrompt from '@/components/consent/ConsentPrompt';
import {
  approveDownstreamErrorConsent,
  denyDownstreamErrorConsent,
  getDownstreamErrorConsentContext,
} from '@/services/consent';

const getErrorDetail = (err: unknown): string | undefined => {
  if (err && typeof err === 'object' && 'detail' in err && typeof err.detail === 'string') {
    return err.detail;
  }
  return undefined;
};

const ConsentDownstreamError: React.FC = () => {
  const [searchParams] = useSearchParams();
  const nonce = searchParams.get('nonce') || '';
  const [context, setContext] = useState<Awaited<ReturnType<typeof getDownstreamErrorConsentContext>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorDetails, setErrorDetails] = useState<string | undefined>(undefined);
  const [approving, setApproving] = useState(false);
  const [denying, setDenying] = useState(false);
  const [denied, setDenied] = useState(false);

  useEffect(() => {
    if (!nonce) {
      setError('This consent link is missing required information.');
      return;
    }
    getDownstreamErrorConsentContext(nonce)
      .then(setContext)
      .catch(err => {
        setError('Unable to load this error request. Please retry from your MCP client.');
        setErrorDetails(getErrorDetail(err));
      });
  }, [nonce]);

  const handleApprove = useCallback(async () => {
    setApproving(true);
    try {
      const { redirect_url } = await approveDownstreamErrorConsent(nonce);
      window.location.assign(redirect_url);
    } catch (err) {
      setError('Unable to send the error to the application. Please retry from your MCP client.');
      setErrorDetails(getErrorDetail(err));
      setApproving(false);
    }
  }, [nonce]);

  const handleDeny = useCallback(async () => {
    setDenying(true);
    try {
      await denyDownstreamErrorConsent(nonce);
      setDenied(true);
    } catch (err) {
      setError('Failed to record your decision. Please retry from your MCP client.');
      setErrorDetails(getErrorDetail(err));
    } finally {
      setDenying(false);
    }
  }, [nonce]);

  const busy = approving || denying;

  return (
    <AuthPageLayout>
      {error ? (
        <ConsentPrompt.Error message={error} details={errorDetails} />
      ) : denied ? (
        <ConsentPrompt.Declined message='The error was not sent. You can close this window.' />
      ) : !context ? (
        <ConsentPrompt.Loading />
      ) : (
        <div className='card p-10 max-w-lg w-full text-center animate-slide-up'>
          <h1 className='text-xl font-semibold text-[var(--jarvis-text-strong)] mb-4'>
            Send OAuth error to the application?
          </h1>
          <p className='text-base text-[var(--jarvis-text)] mb-6'>
            The downstream authorization request failed. Sending this error lets the MCP client recover and register
            again.
          </p>
          <dl className='bg-[var(--jarvis-card-muted)] rounded-lg p-4 mb-6 text-left space-y-3'>
            <div>
              <dt className='text-xs font-semibold text-[var(--jarvis-muted)]'>Error</dt>
              <dd className='font-mono text-sm break-all text-[var(--jarvis-text)]'>{context.error}</dd>
            </div>
            <div>
              <dt className='text-xs font-semibold text-[var(--jarvis-muted)]'>Description</dt>
              <dd className='text-sm break-words text-[var(--jarvis-text)]'>{context.error_description}</dd>
            </div>
            <div>
              <dt className='text-xs font-semibold text-[var(--jarvis-muted)]'>Application redirect target</dt>
              <dd className='font-mono text-sm break-all text-[var(--jarvis-text)]'>{context.redirect_uri}</dd>
            </div>
          </dl>
          <div className='flex gap-3'>
            <button
              type='button'
              onClick={handleDeny}
              disabled={busy}
              className='bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)] flex-1 rounded-lg font-semibold py-3 transition-all duration-200'
            >
              {denying ? 'Cancelling…' : 'Cancel'}
            </button>
            <button type='button' onClick={handleApprove} disabled={busy} className='btn-primary flex-1'>
              {approving ? 'Sending…' : 'Send error to application'}
            </button>
          </div>
          <p className='text-xs text-[var(--jarvis-muted)] mt-6'>
            Only continue if you recognize this application and redirect target.
          </p>
        </div>
      )}
    </AuthPageLayout>
  );
};

export default ConsentDownstreamError;
