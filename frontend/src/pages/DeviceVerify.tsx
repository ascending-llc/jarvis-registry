import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { AuthPageLayout } from '@/components/auth/AuthPageLayout';
import ConsentPrompt from '@/components/consent/ConsentPrompt';
import { APP_ROUTES } from '@/routes';
import { resolveDeviceCode } from '@/services/consent';

const normalizeCode = (raw: string): string => raw.trim().toUpperCase();

const getErrorDetail = (err: unknown): string | undefined => {
  if (err && typeof err === 'object' && 'detail' in err && typeof err.detail === 'string') {
    return err.detail;
  }
  return undefined;
};

const DeviceVerify: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const codeFromUrl = searchParams.get('user_code');

  const [inputCode, setInputCode] = useState('');
  const [resolving, setResolving] = useState(Boolean(codeFromUrl));
  const [error, setError] = useState<string | null>(null);
  const [errorDetails, setErrorDetails] = useState<string | undefined>(undefined);

  const resolveAndForward = useCallback(
    async (code: string) => {
      setResolving(true);
      setError(null);
      try {
        const { nonce } = await resolveDeviceCode(normalizeCode(code));
        navigate(`${APP_ROUTES.consentDownstream}?nonce=${encodeURIComponent(nonce)}`, { replace: true });
      } catch (err) {
        setError('This code is invalid or has expired. Check your device and try again.');
        setErrorDetails(getErrorDetail(err));
        setResolving(false);
      }
    },
    [navigate],
  );

  useEffect(() => {
    if (codeFromUrl) {
      resolveAndForward(codeFromUrl);
    }
  }, [codeFromUrl, resolveAndForward]);

  const handleSubmit = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      if (inputCode.trim()) {
        resolveAndForward(inputCode);
      }
    },
    [inputCode, resolveAndForward],
  );

  if (resolving) {
    return (
      <AuthPageLayout>
        <ConsentPrompt.Loading />
      </AuthPageLayout>
    );
  }

  if (error) {
    return (
      <AuthPageLayout>
        <ConsentPrompt.Error message={error} details={errorDetails} />
      </AuthPageLayout>
    );
  }

  return (
    <AuthPageLayout>
      <form onSubmit={handleSubmit} className='card p-10 max-w-md w-full text-center animate-slide-up'>
        <h1 className='text-xl font-semibold text-[var(--jarvis-text-strong)] mb-4'>Enter your device code</h1>
        <p className='text-base text-[var(--jarvis-text)] mb-6'>
          Enter the code shown on your device to continue.
        </p>
        <input
          type='text'
          value={inputCode}
          onChange={event => setInputCode(event.target.value)}
          placeholder='WDJB-MJHT'
          maxLength={16}
          autoFocus
          required
          aria-label='Device code'
          className='w-full box-border p-3 text-lg tracking-widest text-center uppercase rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text-strong)] mb-6'
        />
        <button type='submit' className='btn-primary w-full shadow-md hover:shadow-lg transition-all duration-200'>
          Continue
        </button>
      </form>
    </AuthPageLayout>
  );
};

export default DeviceVerify;
