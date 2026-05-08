import { ArrowLeftIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import NavMenu from './NavMenu';
import UserProfileMenu from './UserProfileMenu';

const Content: React.FC<any> = ({ setSidebarOpen, sidebarOpen = true }) => {
  const location = useLocation();
  const navigate = useNavigate();

  const isSubPage =
    location.pathname === '/generate-token' ||
    location.pathname === '/server-registry' ||
    location.pathname === '/server-edit' ||
    location.pathname === '/agent-registry' ||
    location.pathname === '/agent-edit' ||
    location.pathname === '/federation-registry' ||
    location.pathname === '/federation-edit';

  const handleGoBack = () => {
    if (window.innerWidth < 768) setSidebarOpen(false);
    navigate(-1);
  };

  return (
    <div className='flex h-full flex-col overflow-x-hidden'>
      {isSubPage ? (
        /* Sub-page: show only Back button */
        <div className='flex-1 overflow-y-auto space-y-6 px-3 py-4'>
          <div className='space-y-2 mb-6'>
            <button
              onClick={handleGoBack}
              className='w-full flex items-center rounded-lg px-2 py-2 text-sm font-medium transition-colors text-[var(--jarvis-text)] hover:bg-[var(--jarvis-card-muted)]'
              tabIndex={0}
              title='Back to Dashboard'
            >
              <ArrowLeftIcon className='h-5 w-5 flex-shrink-0' />
              <div
                className={`flex flex-1 items-center overflow-hidden transition-all duration-300 ${
                  !sidebarOpen ? 'max-w-0 opacity-0 ml-0' : 'max-w-xs opacity-100 ml-3'
                }`}
              >
                <span className='whitespace-nowrap'>Back to Dashboard</span>
              </div>
            </button>
          </div>
        </div>
      ) : (
        /* Dashboard: nav menu + user profile */
        <>
          <NavMenu sidebarOpen={sidebarOpen} setSidebarOpen={setSidebarOpen} />
          <UserProfileMenu sidebarOpen={sidebarOpen} />
        </>
      )}
    </div>
  );
};

export default Content;
