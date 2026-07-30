import ResourceConsentPage from '@/components/consent/ResourceConsentPage';
import { approveServerConsent, denyServerConsent, getServerConsentContext } from '@/services/consent';

const ConsentServer: React.FC = () => {
  return (
    <ResourceConsentPage
      getConsentContext={getServerConsentContext}
      approveConsent={approveServerConsent}
      denyConsent={denyServerConsent}
      getDescription={context => `This will let it call the '${context.server_name}' MCP server on your behalf.`}
    />
  );
};

export default ConsentServer;
