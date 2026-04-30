import {
  Bars3Icon,
  DocumentTextIcon,
  MagnifyingGlassIcon,
  MoonIcon,
  SunIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

import IconButton from '@/components/IconButton';
import ServerConfigModal from '@/components/ServerConfigModal';
import SERVICES from '@/services';
import logo from '../assets/jarvis_logo_w_text_light_bkg.svg';
import { useServer } from '../contexts/ServerContext';
import { useTheme } from '../contexts/ThemeContext';
import Sidebar from './Sidebar';

interface LayoutProps {
  children: React.ReactNode;
}

const Layout: React.FC<LayoutProps> = ({ children }) => {
  const { theme, toggleTheme } = useTheme();
  const { viewMode, searchTerm, setSearchTerm, setCommittedQuery } = useServer();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [version, setVersion] = useState<string | null>(null);
  const [showIntegrationGuide, setShowIntegrationGuide] = useState(false);
  const location = useLocation();

  const isDashboard = location.pathname === '/';
  const showSearch = isDashboard;

  const handleClearSearch = () => {
    setSearchTerm('');
    setCommittedQuery('');
  };

  const getPlaceholder = () => {
    if (viewMode === 'servers') return 'Search servers, descriptions, or tags...';
    if (viewMode === 'agents') return 'Search agents, descriptions, or tags...';
    if (viewMode === 'external') return 'Search external providers...';
    if (viewMode === 'workflow') return 'Search workflows...';
    return 'Search...';
  };

  useEffect(() => {
    getVersion();
  }, []);

  const getVersion = async () => {
    try {
      const result = await SERVICES.SERVER.getVersion();
      setVersion(result.version);
    } catch (error) {
      console.error('Failed to fetch version:', error);
      return null;
    }
  };

  const handleOpenIntegrationGuide = () => {
    setShowIntegrationGuide(true);
  };

  return (
    <div className='min-h-screen overflow-hidden bg-[var(--jarvis-bg)]'>
      {/* Header */}
      <header className='fixed top-0 left-0 right-0 z-50 border-b border-[color:var(--jarvis-border)] bg-[var(--jarvis-bg)] shadow-sm'>
        <div className='pr-4 sm:pr-6 lg:pr-8'>
          <div className='flex justify-between items-center h-16'>
            {/* Left side */}
            <div className='flex items-center h-full'>
              {/* Sidebar toggle button - aligned exactly with collapsed sidebar width (64px) */}
              <div className='w-16 flex justify-center flex-shrink-0'>
                <button
                  className='rounded-md p-1.5 text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)]'
                  onClick={() => setSidebarOpen(!sidebarOpen)}
                >
                  <Bars3Icon className='h-6 w-6' />
                </button>
              </div>

              {/* Logo */}
              <div className='flex items-center ml-2'>
                <Link to='/' className='flex items-center group'>
                  <div className='h-9 w-9 flex items-center justify-center bg-[var(--jarvis-surface)] rounded-xl shadow-sm border border-[color:var(--jarvis-border-strong)] transition-all group-hover:border-[color:var(--jarvis-primary)]'>
                    <img src={logo} alt='Jarvis Registry Logo' className='h-6 w-6' />
                  </div>
                  <span className='ml-3 text-lg font-bold text-[var(--jarvis-text-strong)] tracking-tight transition-colors group-hover:text-[var(--jarvis-primary-text)]'>
                    Jarvis Registry
                  </span>
                </Link>
              </div>
            </div>

            {/* Middle (Search) */}
            {showSearch && (
              <div className='hidden md:flex flex-1 justify-center px-6'>
                <div className='relative w-full max-w-xl'>
                  <div className='absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none'>
                    <MagnifyingGlassIcon className='h-4 w-4 text-[var(--jarvis-subtle)]' />
                  </div>
                  <input
                    type='text'
                    placeholder={getPlaceholder()}
                    className='h-10 w-full rounded-xl border border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-surface)] pl-10 pr-10 text-sm text-[var(--jarvis-text)] outline-none transition placeholder:text-[var(--jarvis-input-placeholder)] focus:border-[color:var(--jarvis-primary)] focus:bg-[var(--jarvis-input-bg-focus)] focus:ring-2 focus:ring-[color:var(--jarvis-primary-soft-hover)] shadow-sm'
                    value={searchTerm}
                    onChange={e => setSearchTerm(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        setCommittedQuery(searchTerm.trim());
                      }
                    }}
                  />
                  {searchTerm && (
                    <button
                      onClick={handleClearSearch}
                      className='absolute inset-y-0 right-0 flex items-center pr-3 text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text)]'
                    >
                      <XMarkIcon className='h-4 w-4' />
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Right side */}
            <div className='flex items-center space-x-4 flex-shrink-0'>
              {/* Version badge */}
              {version && (
                <div className='hidden md:flex items-center rounded-md bg-[var(--jarvis-primary-soft)] px-2.5 py-1'>
                  <span className='text-xs font-medium text-[var(--jarvis-primary-text-hover)]'>{version}</span>
                </div>
              )}

              <IconButton
                ariaLabel='Integration guide'
                tooltip='Integration guide'
                onClick={handleOpenIntegrationGuide}
              >
                <DocumentTextIcon className='h-4 w-4' />
              </IconButton>

              <IconButton
                ariaLabel='Toggle theme'
                tooltip={theme === 'dark' ? 'Toggle theme' : 'Toggle theme'}
                onClick={toggleTheme}
              >
                {theme === 'dark' ? <SunIcon className='h-4 w-4' /> : <MoonIcon className='h-4 w-4' />}
              </IconButton>
            </div>
          </div>
        </div>
      </header>

      <div className='flex h-screen pt-16'>
        {/* Sidebar */}
        <Sidebar sidebarOpen={sidebarOpen} setSidebarOpen={setSidebarOpen} />

        {/* Main content */}
        <main
          className={`flex-1 flex flex-col transition-all duration-300 ${sidebarOpen ? 'md:ml-64 lg:ml-72 xl:ml-80' : 'md:ml-16'}`}
        >
          <div className='flex-1 flex flex-col px-4 sm:px-6 lg:px-8 pt-4 md:pt-8 pb-1 md:pb-2 overflow-hidden'>
            {children}
          </div>

          {/* Footer */}
          <footer className='h-8 flex items-center justify-center px-4 sm:px-6 lg:px-8'>
            <div className='flex items-center gap-2 text-xs text-[var(--jarvis-muted)]'>
              <a
                href='https://ascendingdc.com'
                target='_blank'
                rel='noopener noreferrer'
                className='underline transition-colors hover:text-[var(--jarvis-primary-text)]'
              >
                ASCENDING
              </a>
              <span>|</span>
              <span>Messy World, Clean Code!</span>
              <span>|</span>
              <a
                href='https://app.termly.io/policy-viewer/policy.html?policyUUID=0c91586e-1d8d-489e-83af-70b343467a34'
                target='_blank'
                rel='noopener noreferrer'
                className='underline transition-colors hover:text-[var(--jarvis-primary-text)]'
              >
                Privacy policy
              </a>
              <span>|</span>
              <a
                href='https://app.termly.io/policy-viewer/policy.html?policyUUID=c5f9adb5-4979-4c41-81de-904f87321a4e'
                target='_blank'
                rel='noopener noreferrer'
                className='underline transition-colors hover:text-[var(--jarvis-primary-text)]'
              >
                Terms of service
              </a>
            </div>
          </footer>
        </main>
      </div>

      <ServerConfigModal
        isOpen={showIntegrationGuide}
        onClose={() => setShowIntegrationGuide(false)}
        configScope='registry'
      />
    </div>
  );
};

export default Layout;
