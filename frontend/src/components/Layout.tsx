import { Menu, Transition } from '@headlessui/react';
import {
  ArrowRightStartOnRectangleIcon,
  Bars3Icon,
  Cog6ToothIcon,
  DocumentTextIcon,
  KeyIcon,
  MoonIcon,
  SunIcon,
} from '@heroicons/react/24/outline';
import React, { Fragment, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import IconButton from '@/components/IconButton';
import ServerConfigModal from '@/components/ServerConfigModal';
import SERVICES from '@/services';
import logo from '../assets/jarvis_logo_w_text_light_bkg.svg';
import { useAuth } from '../contexts/AuthContext';
import { useServer } from '../contexts/ServerContext';
import { useTheme } from '../contexts/ThemeContext';
import Sidebar from './Sidebar';

interface LayoutProps {
  children: React.ReactNode;
}

const Layout: React.FC<LayoutProps> = ({ children }) => {
  const { theme, toggleTheme } = useTheme();
  const { user, logout } = useAuth();
  const { activeFilter } = useServer();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [version, setVersion] = useState<string | null>(null);
  const [showIntegrationGuide, setShowIntegrationGuide] = useState(false);

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

  const handleLogout = async () => {
    try {
      await logout();
    } catch (error) {
      console.error('Logout failed:', error);
    }
  };

  const handleOpenIntegrationGuide = () => {
    setShowIntegrationGuide(true);
  };

  return (
    <div className="min-h-screen overflow-hidden bg-[var(--jarvis-bg)]">
      {/* Header */}
      <header className="fixed top-0 left-0 right-0 z-50 border-b border-[color:var(--jarvis-border)] bg-[var(--jarvis-bg)] shadow-sm">
        <div className="px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            {/* Left side */}
            <div className="flex items-center">
              {/* Sidebar toggle button - visible on all screen sizes */}
              <button
                className="mr-2 rounded-md p-2 text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)]"
                onClick={() => setSidebarOpen(!sidebarOpen)}
              >
                <Bars3Icon className="h-6 w-6" />
              </button>

              {/* Logo */}
              <div className="flex items-center ml-2 md:ml-0">
                <Link to='/' className="flex items-center hover:opacity-80 transition-opacity">
                  <img src={logo} alt='Jarvis Registry Logo' className="h-8 w-8" />
                  <span className="ml-2 text-xl font-bold text-[var(--jarvis-text)]">Jarvis Registry</span>
                </Link>
              </div>
            </div>

            {/* Right side */}
            <div className="flex items-center space-x-4">
              {/* Version badge */}
              {version && (
                <div className="hidden md:flex items-center rounded-md bg-[var(--jarvis-primary-soft)] px-2.5 py-1">
                  <span className="text-xs font-medium text-[var(--jarvis-primary-text-hover)]">{version}</span>
                </div>
              )}

              <div className="hidden md:block text-sm font-medium text-[var(--jarvis-muted)]">
                {user?.username || 'Admin'}
              </div>

              <IconButton
                ariaLabel='Integration guide'
                tooltip='Integration guide'
                onClick={handleOpenIntegrationGuide}
              >
                <DocumentTextIcon className="h-4 w-4" />
              </IconButton>

              <IconButton
                ariaLabel='Toggle theme'
                tooltip={theme === 'dark' ? 'Toggle theme' : 'Toggle theme'}
                onClick={toggleTheme}
              >
                {theme === 'dark' ? <SunIcon className="h-4 w-4" /> : <MoonIcon className="h-4 w-4" />}
              </IconButton>

              <Menu as='div' className="relative">
                <Menu.Button className="focus:outline-none">
                  <IconButton ariaLabel='Settings' tooltip='Settings' as='span'>
                    <Cog6ToothIcon className="h-4 w-4" />
                  </IconButton>
                </Menu.Button>

                <Transition
                  as={Fragment}
                  enter='transition ease-out duration-100'
                  enterFrom='transform opacity-0 scale-95'
                  enterTo='transform opacity-100 scale-100'
                  leave='transition ease-in duration-75'
                  leaveFrom='transform opacity-100 scale-100'
                  leaveTo='transform opacity-0 scale-95'
                >
                  <Menu.Items className="absolute right-0 z-10 mt-2 w-48 origin-top-right rounded-md bg-[var(--jarvis-card)] py-1 shadow-lg ring-1 ring-[color:var(--jarvis-border)] focus:outline-none">
                    <Menu.Item>
                      {({ active }) => (
                        <Link
                          to='/generate-token'
                          className={`${
 active ? 'bg-[var(--jarvis-primary-soft)]' : ''
 } flex items-center px-4 py-2 text-sm text-[var(--jarvis-text)]`}
                        >
                          <KeyIcon className="mr-3 h-4 w-4" />
                          Generate Token
                        </Link>
                      )}
                    </Menu.Item>

                    <div className="my-1 border-t border-[color:var(--jarvis-border)]" />

                    <Menu.Item>
                      {({ active }) => (
                        <button
                          onClick={handleLogout}
                          className={`${
 active ? 'bg-[var(--jarvis-primary-soft)]' : ''
 } flex w-full items-center px-4 py-2 text-sm text-[var(--jarvis-text)]`}
                        >
                          <ArrowRightStartOnRectangleIcon className="mr-3 h-4 w-4" />
                          Sign out
                        </button>
                      )}
                    </Menu.Item>
                  </Menu.Items>
                </Transition>
              </Menu>
            </div>
          </div>
        </div>
      </header>

      <div className="flex h-screen pt-16">
        {/* Sidebar */}
        <Sidebar sidebarOpen={sidebarOpen} setSidebarOpen={setSidebarOpen} />

        {/* Main content */}
        <main
          className={`flex-1 flex flex-col transition-all duration-300 ${sidebarOpen ? 'md:ml-64 lg:ml-72 xl:ml-80' : ''}`}
        >
          <div className="flex-1 flex flex-col px-4 sm:px-6 lg:px-8 pt-4 md:pt-8 pb-1 md:pb-2 overflow-hidden">
            {React.cloneElement(children as React.ReactElement, { activeFilter })}
          </div>

          {/* Footer */}
          <footer className="h-8 flex items-center justify-center px-4 sm:px-6 lg:px-8">
            <div className="flex items-center gap-2 text-xs text-[var(--jarvis-muted)]">
              <a
                href='https://ascendingdc.com'
                target='_blank'
                rel='noopener noreferrer'
                className="underline transition-colors hover:text-[var(--jarvis-primary-text)]"
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
                className="underline transition-colors hover:text-[var(--jarvis-primary-text)]"
              >
                Privacy policy
              </a>
              <span>|</span>
              <a
                href='https://app.termly.io/policy-viewer/policy.html?policyUUID=c5f9adb5-4979-4c41-81de-904f87321a4e'
                target='_blank'
                rel='noopener noreferrer'
                className="underline transition-colors hover:text-[var(--jarvis-primary-text)]"
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
