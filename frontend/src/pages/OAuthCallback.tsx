import { CheckCircleIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import logo from '@/assets/logo.svg';

// Common layout components to reduce duplication
const PageLayout: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className='min-h-screen bg-white dark:bg-gray-900 flex flex-col'>
    <header className='bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 py-4 text-center'>
      <img src={logo} alt='LibreChat' className='h-12 w-auto mx-auto' />
    </header>
    <main className='flex-grow flex items-center justify-center px-4 py-8'>{children}</main>
    <Footer />
  </div>
);

const Footer: React.FC = () => (
  <footer className='py-4 text-center text-sm text-gray-500 dark:text-gray-400'>
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
        <div className='card p-10 max-w-md w-full text-center animate-slide-up'>
          <div className='mx-auto mb-8 w-16 h-16 bg-red-600 dark:bg-red-600 rounded-full flex items-center justify-center animate-pulse'>
            <XMarkIcon className='w-10 h-10 text-white' strokeWidth={3} />
          </div>

          <p className='text-base text-gray-600 dark:text-gray-300 mb-6 leading-relaxed'>
            Sorry, there was a problem during the OAuth authorization process
          </p>

          <div className='bg-red-50 dark:bg-red-900/20 text-red-800 dark:text-red-300 p-4 rounded-lg text-sm mb-6 font-mono break-words text-left'>
            <strong className='block mb-2'>Error Details:</strong>
            {error}
          </div>

          <div className='flex gap-3 justify-center flex-wrap'>
            <Link
              to='/'
              className='btn-primary px-6 py-3 hover:transform hover:-translate-y-0.5 transition-all duration-200 shadow-md hover:shadow-lg inline-block'
            >
              Retry Authorization
            </Link>
            <button
              onClick={goToDashboard}
              className='bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 px-6 py-3 rounded-lg font-semibold hover:bg-gray-200 dark:hover:bg-gray-600 transition-all duration-200'
            >
              Go to the Dashboard page
            </button>
          </div>

          <div className='text-xs text-gray-500 dark:text-gray-400 mt-6 pt-6 border-t border-gray-200 dark:border-gray-700'>
            <p>If the problem persists, please contact the system administrator</p>
            <p className='mt-2'>
              Error Code: <code className='bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded'>{error}</code>
            </p>
          </div>
        </div>
      </PageLayout>
    );
  }

  // Render success state (default)
  return (
    <PageLayout>
      <div className='card p-10 max-w-md w-full text-center animate-slide-up'>
        <div className='mx-auto mb-8 w-16 h-16 bg-green-700 dark:bg-primary-600 rounded-full flex items-center justify-center animate-pulse'>
          <CheckCircleIcon className='w-10 h-10 text-white' />
        </div>

        <h1 className='text-3xl font-semibold text-gray-900 dark:text-white mb-6'>Authentication Successful</h1>

        <p className='text-base text-gray-600 dark:text-gray-300 mb-6 leading-relaxed'>
          You've been authenticated for{' '}
          <span className='inline-block font-semibold text-green-600 dark:text-primary-400 bg-green-50 dark:bg-primary-900/20 px-3 py-1 rounded-md mx-1'>
            {serverPath}
          </span>
        </p>

        <p className='text-base text-gray-600 dark:text-gray-300 mb-6 leading-relaxed'>
          Your credentials have been securely saved. You can now close this window and retry your original command.
        </p>

        <button
          onClick={goToDashboard}
          className='btn-primary w-full mt-6 hover:transform hover:-translate-y-0.5 transition-all duration-200 shadow-md hover:shadow-lg'
        >
          Go to the Dashboard page
        </button>

        <div className='text-xs text-gray-500 dark:text-gray-400 mt-6 opacity-80'>
          This window will close automatically in {countdown} seconds
        </div>
      </div>
    </PageLayout>
  );
};

export default OAuthCallback;
