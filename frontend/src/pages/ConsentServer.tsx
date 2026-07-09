import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { AuthPageLayout } from '@/components/auth/AuthPageLayout';
import ConsentPrompt from '@/components/consent/ConsentPrompt';
import { approveServerConsent, getServerConsentContext } from '@/services/consent';

const getErrorDetail = (err: unknown): string | undefined => {
  if (err && typeof err === 'object' && 'detail' in err && typeof err.detail === 'string') {
    return err.detail;
  }
  return undefined;
};

const DEEP_LINK_BRANDS = ['cursor', 'vscode', 'claude'];

const ConsentServer: React.FC = () => {
  const [searchParams] = useSearchParams();
  const nonce = searchParams.get('nonce') || '';
  const [context, setContext] = useState<Awaited<ReturnType<typeof getServerConsentContext>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorDetails, setErrorDetails] = useState<string | undefined>(undefined);
  const [approving, setApproving] = useState(false);
  const [approved, setApproved] = useState(false);
  const [clientBranding, setClientBranding] = useState<string | null>(null);

  useEffect(() => {
    if (!nonce) {
      setError('This consent link is missing required information.');
      return;
    }
    getServerConsentContext(nonce)
      .then(setContext)
      .catch(err => {
        setError('Unable to load this consent request. Please retry your request.');
        setErrorDetails(getErrorDetail(err));
      });
  }, [nonce]);

  const handleApprove = useCallback(async () => {
    setApproving(true);
    try {
      const result = await approveServerConsent(nonce);
      setClientBranding(result.client_branding);
      setApproved(true);
    } catch (err) {
      setError('Authorization failed. Please retry your request.');
      setErrorDetails(getErrorDetail(err));
    } finally {
      setApproving(false);
    }
  }, [nonce]);

  // Deep link back to the MCP client (VS Code, Claude, Cursor) once consent is granted,
  // mirroring OAuthCallback.tsx's post-authorization deep link.
  useEffect(() => {
    if (approved && clientBranding && DEEP_LINK_BRANDS.includes(clientBranding)) {
      const deepLinkTimer = setTimeout(() => {
        const link = document.createElement('a');
        link.href = `${clientBranding}://`;
        link.click();
      }, 1000); // Give user time to see success message

      return () => clearTimeout(deepLinkTimer);
    }
  }, [approved, clientBranding]);

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
