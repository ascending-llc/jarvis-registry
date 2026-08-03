import ResourceConsentPage from '@/components/consent/ResourceConsentPage';
import { approveAgentConsent, denyAgentConsent, getAgentConsentContext } from '@/services/consent';

const ConsentAgent: React.FC = () => {
  return (
    <ResourceConsentPage
      getConsentContext={getAgentConsentContext}
      approveConsent={approveAgentConsent}
      denyConsent={denyAgentConsent}
      getDescription={context => `This will let it call the '${context.agent_name}' A2A agent on your behalf.`}
    />
  );
};

export default ConsentAgent;
