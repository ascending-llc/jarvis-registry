import { Navigate } from 'react-router-dom';
import { captureReturnPath } from '../config';
import { useAuth } from '../contexts/AuthContext';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className='min-h-screen flex items-center justify-center'>
        <div className='animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600'></div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to={`/login?next=${encodeURIComponent(captureReturnPath())}`} replace />;
  }

  return <>{children}</>;
};

export default ProtectedRoute;
