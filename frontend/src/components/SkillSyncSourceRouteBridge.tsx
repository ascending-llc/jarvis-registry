import { Navigate, useLocation, useParams } from 'react-router-dom';

import { APP_ROUTES } from '@/routes';

interface SkillSyncSourceRouteBridgeProps {
  list?: boolean;
}

/**
 * The backend OAuth callback redirects to /skill-sync-sources[/:id]; map those onto the external
 * providers tab and the GitHub provider page, keeping query params such as ?status=connected.
 */
const SkillSyncSourceRouteBridge = ({ list = false }: SkillSyncSourceRouteBridgeProps) => {
  const { id } = useParams();
  const location = useLocation();
  const params = new URLSearchParams(location.search);

  if (list) {
    params.set('tab', 'external');
    return <Navigate replace to={`${APP_ROUTES.root}?${params.toString()}`} />;
  }

  if (!id) return <Navigate replace to={`${APP_ROUTES.root}?tab=external`} />;
  params.set('id', id);
  params.set('provider', 'github');
  return <Navigate replace to={`${APP_ROUTES.federationEdit}?${params.toString()}`} />;
};

export default SkillSyncSourceRouteBridge;
