import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { APP_ROUTES } from '@/routes';
import { captureExplicitAuthReturnTo } from '@/utils/authReturnTo';
import { useAuth } from '../contexts/AuthContext';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

const LoadingScreen: React.FC = () => (
  <div className='min-h-screen flex items-center justify-center'>
    <div className='animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600'></div>
  </div>
);

const UnauthenticatedRedirect: React.FC = () => {
  const navigate = useNavigate();

  useEffect(() => {
    captureExplicitAuthReturnTo();
    navigate(APP_ROUTES.login, { replace: true });
  }, [navigate]);

  return <LoadingScreen />;
};

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => {
  const { user, loading } = useAuth();

  if (loading) return <LoadingScreen />;

  if (!user) return <UnauthenticatedRedirect />;

  return <>{children}</>;
};

export default ProtectedRoute;
