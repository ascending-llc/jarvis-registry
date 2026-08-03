import { ExclamationTriangleIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { Link } from 'react-router-dom';
import { AuthPageLayout } from '@/components/auth/AuthPageLayout';
import { APP_ROUTES } from '@/routes';

const NotFound: React.FC = () => (
  <AuthPageLayout>
    <div className='sm:mx-auto sm:w-full sm:max-w-md'>
      <div className='card p-8 text-center'>
        <ExclamationTriangleIcon className='h-10 w-10 mx-auto text-[var(--jarvis-muted)]' />
        <h2 className='mt-4 text-2xl font-bold text-[var(--jarvis-text-strong)]'>Page not found</h2>
        <p className='mt-2 text-sm text-[var(--jarvis-muted)]'>
          The page you're looking for doesn't exist or may have been moved.
        </p>
        <Link
          to={APP_ROUTES.root}
          className='mt-6 inline-flex items-center justify-center px-4 py-3 border border-[color:var(--jarvis-border)] rounded-lg shadow-sm text-sm font-medium text-[var(--jarvis-text)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-card-muted)] transition-all duration-200 hover:shadow-md'
        >
          Back to dashboard
        </Link>
      </div>
    </div>
  </AuthPageLayout>
);

export default NotFound;
