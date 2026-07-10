import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { AuthPageLayout } from '@/components/auth/AuthPageLayout';
import ConsentPrompt from '@/components/consent/ConsentPrompt';
import { approveServerConsent, DEEP_LINK_BRANDS, denyServerConsent, getServerConsentContext } from '@/services/consent';

const getErrorDetail = (err: unknown): string | undefined => {
  if (err && typeof err === 'object' && 'detail' in err && typeof err.detail === 'string') {
    return err.detail;
  }
  return undefined;
};

const ConsentServer: React.FC = () => {
  const [searchParams] = useSearchParams();
  const nonce = searchParams.get('nonce') || '';
  const [context, setContext] = useState<Awaited<ReturnType<typeof getServerConsentContext>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorDetails, setErrorDetails] = useState<string | undefined>(undefined);
  const [approving, setApproving] = useState(false);
  const [approved, setApproved] = useState(false);
  const [denying, setDenying] = useState(false);
  const [denied, setDenied] = useState(false);
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

  const handleDeny = useCallback(async () => {
    setDenying(true);
    try {
      const result = await denyServerConsent(nonce);
      setClientBranding(result.client_branding);
      setDenied(true);
    } catch (err) {
      setError('Failed to record your decision. Please retry your request.');
      setErrorDetails(getErrorDetail(err));
    } finally {
      setDenying(false);
    }
  }, [nonce]);

  // Deep link back to the MCP client (VS Code, Claude, Cursor) once the decision is recorded
  // (approve or deny), mirroring OAuthCallback.tsx's post-authorization deep link. The MCP client
  // is only ever told "the human responded" either way — its retry is what surfaces the outcome.
  useEffect(() => {
    if ((approved || denied) && clientBranding && DEEP_LINK_BRANDS.includes(clientBranding)) {
      const deepLinkTimer = setTimeout(() => {
        const link = document.createElement('a');
        link.href = `${clientBranding}://`;
        link.click();
      }, 1000); // Give user time to see the result message

      return () => clearTimeout(deepLinkTimer);
    }
  }, [approved, denied, clientBranding]);

  return (
    <AuthPageLayout>
      {error ? (
        <ConsentPrompt.Error message={error} details={errorDetails} />
      ) : approved ? (
        <ConsentPrompt.Success
          message='You can now close this window and retry your original command.'
          submessage='Your explicit consent has been securely recorded.'
        />
      ) : denied ? (
        <ConsentPrompt.Declined message="You can close this window. If this application tries again, you'll be asked to review this request again." />
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
          onDeny={handleDeny}
          approving={approving}
          denying={denying}
        />
      )}
    </AuthPageLayout>
  );
};

export default ConsentServer;
