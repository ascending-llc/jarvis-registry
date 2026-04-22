import { ExclamationTriangleIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import SERVICES from '@/services';
import { getBasePath } from '../config';

interface OAuthProvider {
  name: string;
  display_name: string;
  icon?: string;
}

const Login: React.FC = () => {
  const [error, setError] = useState('');
  const [oauthProviders, setOauthProviders] = useState<OAuthProvider[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(true);
  const [loginInProgress, setLoginInProgress] = useState<string | null>(null);
  const [searchParams] = useSearchParams();

  useEffect(() => {
    console.log('[Login] Component mounted, fetching OAuth providers...');
    fetchOAuthProviders();

    // Check for error parameter from URL (e.g., from OAuth callback)
    const urlError = searchParams.get('error');
    if (urlError) {
      setError(decodeURIComponent(urlError));
    }
  }, [searchParams]);

  // Log when oauthProviders state changes
  useEffect(() => {
    console.log('[Login] oauthProviders state changed:', oauthProviders);
  }, [oauthProviders]);

  const fetchOAuthProviders = async () => {
    setLoadingProviders(true);
    try {
      console.log('[Login] Fetching OAuth providers from /api/auth/providers');
      const response = await SERVICES.AUTH.getAuthProviders();
      console.log('[Login] Response received:', response);
      console.log('[Login] Providers:', response.providers);
      setOauthProviders(response.providers || []);
      console.log('[Login] State updated with', response.providers?.length || 0, 'providers');
    } catch (error) {
      console.error('[Login] Failed to fetch OAuth providers:', error);
      setError('Failed to load authentication providers. Please refresh the page.');
    } finally {
      setLoadingProviders(false);
    }
  };

  const handleOAuthLogin = (provider: string) => {
    setLoginInProgress(provider);
    window.location.href = `${getBasePath()}/redirect/${provider}`;
  };

  const LoadingSpinner = () => (
    <svg className="animate-spin h-5 w-5" xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24'>
      <circle className="opacity-25" cx='12' cy='12' r='10' stroke='currentColor' strokeWidth='4'></circle>
      <path
        className="opacity-75"
        fill='currentColor'
        d='M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z'
      ></path>
    </svg>
  );

  return (
    <div className="min-h-screen bg-[var(--jarvis-bg)] bg-[var(--jarvis-card)] flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <h2 className="text-center text-3xl font-bold text-[var(--jarvis-text-strong)]">
          Sign in to MCP Servers & A2A Agents Registry
        </h2>
        <p className="mt-2 text-center text-sm text-[var(--jarvis-muted)]">
          Access your MCP server management dashboard
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="card p-8">
          {error && (
            <div className="mb-6 p-4 text-sm text-[var(--jarvis-danger-text)] bg-[var(--jarvis-danger-soft)] border border-[color:var(--jarvis-danger-soft)] rounded-lg bg-[var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)] border-[color:var(--jarvis-danger-soft)] flex items-start space-x-2">
              <ExclamationTriangleIcon className="h-5 w-5 flex-shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          {loadingProviders ? (
            <div className="flex flex-col items-center justify-center py-8 space-y-4">
              <LoadingSpinner />
              <p className="text-sm text-[var(--jarvis-muted)]">Loading authentication providers...</p>
            </div>
          ) : oauthProviders.length > 0 ? (
            <div className="space-y-3">
              {oauthProviders.map(provider => (
                <button
                  key={provider.name}
                  onClick={() => handleOAuthLogin(provider.name)}
                  disabled={loginInProgress !== null}
                  className="w-full flex items-center justify-center px-4 py-3 border border-[color:var(--jarvis-border)] rounded-lg shadow-sm text-sm font-medium text-[var(--jarvis-text)] bg-[var(--jarvis-card)] bg-[var(--jarvis-card-muted)] hover:bg-[var(--jarvis-card-muted)] transition-all duration-200 hover:shadow-md disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {loginInProgress === provider.name ? (
                    <>
                      <LoadingSpinner />
                      <span className="ml-2">Redirecting...</span>
                    </>
                  ) : (
                    <span>Continue with {provider.display_name}</span>
                  )}
                </button>
              ))}
            </div>
          ) : (
            <div className="text-center py-8">
              <p className="text-sm text-[var(--jarvis-muted)]">
                No authentication providers available. Please contact your administrator.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Login;
