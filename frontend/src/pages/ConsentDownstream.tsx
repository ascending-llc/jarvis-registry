import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { AuthPageLayout } from '@/components/auth/AuthPageLayout';
import ConsentPrompt from '@/components/consent/ConsentPrompt';
import { approveDownstreamConsent, getDownstreamConsentContext } from '@/services/consent';

const getErrorDetail = (err: unknown): string | undefined => {
  if (err && typeof err === 'object' && 'detail' in err && typeof err.detail === 'string') {
    return err.detail;
  }
  return undefined;
};

const ConsentDownstream: React.FC = () => {
  const [searchParams] = useSearchParams();
  const nonce = searchParams.get('nonce') || '';
  const [context, setContext] = useState<Awaited<ReturnType<typeof getDownstreamConsentContext>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorDetails, setErrorDetails] = useState<string | undefined>(undefined);
  const [approving, setApproving] = useState(false);

  useEffect(() => {
    if (!nonce) {
      setError('This consent link is missing required information.');
      return;
    }
    getDownstreamConsentContext(nonce)
      .then(setContext)
      .catch(err => {
        setError('Unable to load this consent request. Please retry from your MCP client.');
        setErrorDetails(getErrorDetail(err));
      });
  }, [nonce]);

  const handleApprove = useCallback(async () => {
    setApproving(true);
    try {
      const { redirect_url } = await approveDownstreamConsent(nonce);
      window.location.assign(redirect_url);
    } catch (err) {
      setError('Authorization failed. Please retry from your MCP client.');
      setErrorDetails(getErrorDetail(err));
      setApproving(false);
    }
  }, [nonce]);

  return (
    <AuthPageLayout>
      {error ? (
        <ConsentPrompt.Error message={error} details={errorDetails} />
      ) : !context ? (
        <ConsentPrompt.Loading />
      ) : (
        <ConsentPrompt
          clientName={context.client_name}
          clientUri={context.client_uri}
          ipAddress={context.ip_address}
          registeredAt={context.registered_at}
          description='This will let it obtain an access token to connect to a downstream MCP server on your behalf.'
          onApprove={handleApprove}
          approving={approving}
        />
      )}
    </AuthPageLayout>
  );
};

export default ConsentDownstream;
