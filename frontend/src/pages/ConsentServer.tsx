import { isAxiosError } from 'axios';
import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { AuthPageLayout } from '@/components/auth/AuthPageLayout';
import ConsentPrompt from '@/components/consent/ConsentPrompt';
import { approveServerConsent, getServerConsentContext } from '@/services/consent';

const ConsentServer: React.FC = () => {
  const [searchParams] = useSearchParams();
  const nonce = searchParams.get('nonce') || '';
  const [context, setContext] = useState<Awaited<ReturnType<typeof getServerConsentContext>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorDetails, setErrorDetails] = useState<string | undefined>(undefined);
  const [approving, setApproving] = useState(false);
  const [approved, setApproved] = useState(false);

  useEffect(() => {
    if (!nonce) {
      setError('This consent link is missing required information.');
      return;
    }
    getServerConsentContext(nonce)
      .then(setContext)
      .catch(err => {
        setError('This consent link has expired. Please retry your request.');
        if (isAxiosError(err) && err.response?.data?.detail) {
          setErrorDetails(err.response.data.detail);
        }
      });
  }, [nonce]);

  const handleApprove = useCallback(async () => {
    setApproving(true);
    try {
      await approveServerConsent(nonce);
      setApproved(true);
    } catch (err) {
      setError('Authorization failed. Please retry your request.');
      if (isAxiosError(err) && err.response?.data?.detail) {
        setErrorDetails(err.response.data.detail);
      }
    } finally {
      setApproving(false);
    }
  }, [nonce]);

  return (
    <AuthPageLayout>
      {error ? (
        <ConsentPrompt.Error message={error} details={errorDetails} />
      ) : approved ? (
        <ConsentPrompt.Success
          message='You can now close this window and retry your original command.'
          submessage='Your explicit consent has been securely recorded.'
        />
      ) : !context ? (
        <ConsentPrompt.Loading />
      ) : (
        <ConsentPrompt
          clientName={context.client_name}
          clientUri={context.client_uri}
          ipAddress={context.ip_address}
          registeredAt={context.registered_at}
          description={`This will let it call the '${context.server_name}' MCP server on your behalf.`}
          onApprove={handleApprove}
          approving={approving}
        />
      )}
    </AuthPageLayout>
  );
};

export default ConsentServer;
