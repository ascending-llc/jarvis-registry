import { CheckCircleIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import logo from '@/assets/jarvis_logo_w_text_light_bkg.svg';

// Common layout components to reduce duplication
const PageLayout: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="min-h-screen bg-[var(--jarvis-card)] bg-[var(--jarvis-card)] flex flex-col">
    <header className="bg-[var(--jarvis-card)] bg-[var(--jarvis-card)] border-b border-[color:var(--jarvis-border)] border-[color:var(--jarvis-border)] py-4 text-center">
      <img src={logo} alt='Jarvis Registry Logo' className="h-12 w-auto mx-auto" />
    </header>
    <main className="flex-grow flex items-center justify-center px-4 py-8">{children}</main>
    <Footer />
  </div>
);

const Footer: React.FC = () => (
  <footer className="py-4 text-center text-sm text-[var(--jarvis-muted)] text-[var(--jarvis-muted)]">
    © {new Date().getFullYear()} Jarvis. All rights reserved.
  </footer>
);

const OAuthCallback: React.FC = () => {
  const [searchParams] = useSearchParams();
  const [countdown, setCountdown] = useState(5);
  const navigate = useNavigate();

  // Get URL parameters with memoization
  const type = useMemo(() => searchParams.get('type') || 'success', [searchParams]);
  const serverPath = useMemo(() => searchParams.get('serverPath') || 'Connectors', [searchParams]);
  const error = useMemo(() => searchParams.get('error') || 'Unknown error occurred', [searchParams]);
  const clientBranding = useMemo(() => searchParams.get('clientBranding') ?? '', [searchParams]);

  const goToDashboard = useCallback(() => {
    navigate('/');
  }, [navigate]);

  // Handle deep link for supported clients (cursor, vscode, claude)
  useEffect(() => {
    if (type === 'success' && ['cursor', 'vscode', 'claude'].includes(clientBranding)) {
      const deepLinkTimer = setTimeout(() => {
        const link = document.createElement('a');
        link.href = `${clientBranding}://`;
        link.click();
      }, 1000); // Give user time to see success message

      return () => clearTimeout(deepLinkTimer);
    }
  }, [type, clientBranding]);

  useEffect(() => {
    const timer = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) {
          goToDashboard();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    let timerCloseWindow: NodeJS.Timeout;
    const handleVisibilityChange = () => {
      if (document.hidden) {
        if (timerCloseWindow) clearTimeout(timerCloseWindow);
        timerCloseWindow = setTimeout(goToDashboard, 1000);
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      clearInterval(timer);
      if (timerCloseWindow) clearTimeout(timerCloseWindow);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [goToDashboard]);

  // Render error state
  if (type === 'error') {
    return (
      <PageLayout>
        <div className="card p-10 max-w-md w-full text-center animate-slide-up">
          <div className="mx-auto mb-8 w-16 h-16 bg-[var(--jarvis-danger)] dark:bg-[var(--jarvis-danger)] rounded-full flex items-center justify-center animate-pulse">
            <XMarkIcon className="w-10 h-10 text-white" strokeWidth={3} />
          </div>

          <p className="text-base text-[var(--jarvis-muted)] text-[var(--jarvis-text)] mb-6 leading-relaxed">
            Sorry, there was a problem during the OAuth authorization process
          </p>

          <div className="bg-[var(--jarvis-danger-soft)] bg-[var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)] p-4 rounded-lg text-sm mb-6 font-mono break-words text-left">
            <strong className="block mb-2">Error Details:</strong>
            {error}
          </div>

          <div className="flex gap-3 justify-center flex-wrap">
            <Link
              to='/'
              className="btn-primary px-6 py-3 hover:transform hover:-translate-y-0.5 transition-all duration-200 shadow-md hover:shadow-lg inline-block"
            >
              Retry Authorization
            </Link>
            <button
              onClick={goToDashboard}
              className="bg-[var(--jarvis-card-muted)] bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)] text-[var(--jarvis-text)] px-6 py-3 rounded-lg font-semibold hover:bg-[var(--jarvis-card-muted)] hover:bg-[var(--jarvis-card-muted)] transition-all duration-200"
            >
              Go to the Dashboard page
            </button>
          </div>

          <div className="text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-muted)] mt-6 pt-6 border-t border-[color:var(--jarvis-border)] border-[color:var(--jarvis-border)]">
            <p>If the problem persists, please contact the system administrator</p>
            <p className="mt-2">
              Error Code: <code className="bg-[var(--jarvis-card-muted)] bg-[var(--jarvis-card)] px-2 py-1 rounded">{error}</code>
            </p>
          </div>
        </div>
      </PageLayout>
    );
  }

  // Render success state (default)
  return (
    <PageLayout>
      <div className="card p-10 max-w-md w-full text-center animate-slide-up">
        <div className="mx-auto mb-8 w-16 h-16 bg-[var(--jarvis-success)] dark:bg-[var(--jarvis-primary)] rounded-full flex items-center justify-center animate-pulse">
          <CheckCircleIcon className="w-10 h-10 text-white" />
        </div>

        <h1 className="text-3xl font-semibold text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)] mb-6">Authentication Successful</h1>

        <p className="text-base text-[var(--jarvis-muted)] text-[var(--jarvis-text)] mb-6 leading-relaxed">
          You've been authenticated for{' '}
          <span className="inline-block font-semibold text-[var(--jarvis-success-text)] dark:text-[var(--jarvis-primary-text)] bg-[var(--jarvis-success-soft)] bg-[var(--jarvis-primary-soft)] px-3 py-1 rounded-md mx-1">
            {serverPath}
          </span>
        </p>

        <p className="text-base text-[var(--jarvis-muted)] text-[var(--jarvis-text)] mb-6 leading-relaxed">
          Your credentials have been securely saved. You can now close this window and retry your original command.
        </p>

        <button
          onClick={goToDashboard}
          className="btn-primary w-full mt-6 hover:transform hover:-translate-y-0.5 transition-all duration-200 shadow-md hover:shadow-lg"
        >
          Go to the Dashboard page
        </button>

        <div className="text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-muted)] mt-6 opacity-80">
          This window will close automatically in {countdown} seconds
        </div>
      </div>
    </PageLayout>
  );
};

export default OAuthCallback;
