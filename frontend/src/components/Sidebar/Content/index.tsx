import { Menu, Transition } from '@headlessui/react';
import {
  ArrowLeftIcon,
  ArrowRightStartOnRectangleIcon,
  ChevronUpDownIcon,
  GlobeAltIcon,
  KeyIcon,
  QuestionMarkCircleIcon,
  QueueListIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';

import AgentIcon from '@/assets/AgentIcon';
import McpIcon from '@/assets/McpIcon';
import { useAuth } from '@/contexts/AuthContext';
import { useServer } from '@/contexts/ServerContext';

const Content: React.FC<any> = ({ setSidebarOpen, sidebarOpen = true }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const [, setSearchParams] = useSearchParams();
  const { user, logout } = useAuth();
  const { stats, agentStats, federationStats, viewMode, setViewMode, activeFilter, setActiveFilter } = useServer();

  const isTokenPage = location.pathname === '/generate-token';
  const isServerRegistryOrEditPage = location.pathname === '/server-registry' || location.pathname === '/server-edit';
  const isAgentRegistryOrEditPage = location.pathname === '/agent-registry' || location.pathname === '/agent-edit';
  const isFederationRegistryOrEditPage =
    location.pathname === '/federation-registry' || location.pathname === '/federation-edit';

  const handleGoBack = () => {
    if (window.innerWidth < 768) setSidebarOpen(false);
    navigate(-1);
  };

  const handleNavigation = (mode: 'servers' | 'agents' | 'workflow' | 'external') => {
    if (viewMode === mode) {
      // Toggle sidebar if clicking the already active menu
      if (window.innerWidth >= 768) {
        setSidebarOpen(!sidebarOpen);
      } else {
        setSidebarOpen(false);
      }
      return;
    }
    setViewMode(mode);
    setSearchParams(mode === 'servers' ? {} : { tab: mode }, { replace: true });
    if (window.innerWidth < 768) setSidebarOpen(false);
  };

  const handleLogout = async () => {
    try {
      await logout();
    } catch (error) {
      console.error('Logout failed:', error);
    }
  };

  // Helper to parse a clean first name from username (handles email formats e.g. ryo.h@ascendingdc.com -> Ryo)
  const getFirstName = () => {
    if (!user?.username) return 'User';
    if (user.username.includes('@')) {
      const namePart = user.username.split('@')[0];
      const first = namePart.split('.')[0];
      return first.charAt(0).toUpperCase() + first.slice(1);
    }
    if (user.username.includes(' ')) {
      return user.username.split(' ')[0];
    }
    if (user.username.includes('.')) {
      const first = user.username.split('.')[0];
      return first.charAt(0).toUpperCase() + first.slice(1);
    }
    return user.username;
  };

  const getUserEmail = () => {
    if (user?.email) return user.email;
    if (user?.username && user.username.includes('@')) return user.username;
    return '';
  };

  /** List of filters */
  const filters = [
    { key: 'all', label: 'All', colorClass: 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)]' },
    {
      key: 'enabled',
      label: 'Active',
      colorClass: 'bg-[var(--jarvis-success-soft)] text-[var(--jarvis-success-text)]',
    },
    { key: 'disabled', label: 'Inactive', colorClass: 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-muted)]' },
    {
      key: 'unhealthy',
      label: 'Issues',
      colorClass: 'bg-[var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)]',
    },
  ];

  return (
    <div className='flex h-full flex-col overflow-x-hidden'>
      {/* Conditional Content (Sub-pages Navigation) */}
      {isServerRegistryOrEditPage || isAgentRegistryOrEditPage || isFederationRegistryOrEditPage || isTokenPage ? (
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
        /* Dashboard Sidebar Layout */
        <>
          <div className='flex-1 overflow-y-auto px-3 py-4'>
            {/* Resources Section */}
            <div className='space-y-1'>
              <div
                className={`text-xs font-semibold uppercase tracking-wider text-[var(--jarvis-faint)] overflow-hidden transition-all duration-300 ${
                  !sidebarOpen ? 'max-h-0 opacity-0 mb-0' : 'max-h-8 opacity-100 mb-2'
                }`}
              >
                <div className='px-3 pt-1'>Resources</div>
              </div>

              {/* MCP Servers */}
              <button
                onClick={() => handleNavigation('servers')}
                className={`w-full flex items-center rounded-lg px-2 py-2 text-sm font-medium transition-colors ${
                  viewMode === 'servers'
                    ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                    : 'text-[var(--jarvis-muted)] hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-text)]'
                }`}
                title='MCP Servers'
              >
                <McpIcon className='h-5 w-5 flex-shrink-0' />
                <div
                  className={`flex flex-1 items-center justify-between overflow-hidden transition-all duration-300 ${
                    !sidebarOpen ? 'max-w-0 opacity-0 ml-0' : 'max-w-xs opacity-100 ml-3'
                  }`}
                >
                  <span className='whitespace-nowrap'>MCP Servers</span>
                  <span className='rounded-full bg-[var(--jarvis-bg)] px-2 py-0.5 text-xs font-semibold text-[var(--jarvis-muted)]'>
                    {stats.total}
                  </span>
                </div>
              </button>

              {/* A2A Agents */}
              <button
                onClick={() => handleNavigation('agents')}
                className={`w-full flex items-center rounded-lg px-2 py-2 text-sm font-medium transition-colors ${
                  viewMode === 'agents'
                    ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                    : 'text-[var(--jarvis-muted)] hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-text)]'
                }`}
                title='A2A Agents'
              >
                <AgentIcon className='h-5 w-5 flex-shrink-0' />
                <div
                  className={`flex flex-1 items-center justify-between overflow-hidden transition-all duration-300 ${
                    !sidebarOpen ? 'max-w-0 opacity-0 ml-0' : 'max-w-xs opacity-100 ml-3'
                  }`}
                >
                  <span className='whitespace-nowrap'>A2A Agents</span>
                  <span className='rounded-full bg-[var(--jarvis-bg)] px-2 py-0.5 text-xs font-semibold text-[var(--jarvis-muted)]'>
                    {agentStats.total}
                  </span>
                </div>
              </button>

              {/* Workflow */}
              <button
                onClick={() => handleNavigation('workflow')}
                className={`w-full flex items-center rounded-lg px-2 py-2 text-sm font-medium transition-colors ${
                  viewMode === 'workflow'
                    ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                    : 'text-[var(--jarvis-muted)] hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-text)]'
                }`}
                title='Workflow'
              >
                <QueueListIcon className='h-5 w-5 flex-shrink-0' />
                <div
                  className={`flex flex-1 items-center justify-between overflow-hidden transition-all duration-300 ${
                    !sidebarOpen ? 'max-w-0 opacity-0 ml-0' : 'max-w-xs opacity-100 ml-3'
                  }`}
                >
                  <span className='whitespace-nowrap'>Workflow</span>
                  <span className='rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider bg-[var(--jarvis-info-soft)] text-[var(--jarvis-info-text)]'>
                    BETA
                  </span>
                </div>
              </button>

              {/* External Providers */}
              <button
                onClick={() => handleNavigation('external')}
                className={`w-full flex items-center rounded-lg px-2 py-2 text-sm font-medium transition-colors ${
                  viewMode === 'external'
                    ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                    : 'text-[var(--jarvis-muted)] hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-text)]'
                }`}
                title='External Providers'
              >
                <GlobeAltIcon className='h-5 w-5 flex-shrink-0' />
                <div
                  className={`flex flex-1 items-center justify-between overflow-hidden transition-all duration-300 ${
                    !sidebarOpen ? 'max-w-0 opacity-0 ml-0' : 'max-w-xs opacity-100 ml-3'
                  }`}
                >
                  <span className='whitespace-nowrap'>External Providers</span>
                  <span className='rounded-full bg-[var(--jarvis-bg)] px-2 py-0.5 text-xs font-semibold text-[var(--jarvis-muted)]'>
                    {federationStats?.total || 0}
                  </span>
                </div>
              </button>
            </div>

            {/* Filter by status Section */}
            <div
              className={`space-y-1 overflow-hidden transition-all duration-300 ${
                !sidebarOpen ? 'max-h-0 opacity-0 mt-0' : 'max-h-[500px] opacity-100 mt-6'
              }`}
            >
              <div className='px-3 mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--jarvis-faint)]'>
                Filter by status
              </div>
              <div className='space-y-1'>
                {filters.map(filter => {
                  let count = 0;
                  if (viewMode === 'servers') {
                    if (filter.key === 'all') count = stats.total;
                    if (filter.key === 'enabled') count = stats.enabled;
                    if (filter.key === 'disabled') count = stats.disabled;
                    if (filter.key === 'unhealthy') count = stats.withIssues;
                  } else if (viewMode === 'agents') {
                    if (filter.key === 'all') count = agentStats.total;
                    if (filter.key === 'enabled') count = agentStats.enabled;
                    if (filter.key === 'disabled') count = agentStats.disabled;
                    if (filter.key === 'unhealthy') count = agentStats.withIssues;
                  } else if (viewMode === 'external' && federationStats) {
                    if (filter.key === 'all') count = federationStats.total;
                    if (filter.key === 'enabled') count = federationStats.enabled;
                    if (filter.key === 'disabled') count = federationStats.disabled;
                    if (filter.key === 'unhealthy') count = federationStats.withIssues;
                  }

                  return (
                    <button
                      key={filter.key}
                      onClick={() => setActiveFilter(filter.key)}
                      className={`w-full flex items-center px-2 py-1.5 rounded-md text-sm transition-colors ${
                        activeFilter === filter.key
                          ? 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)]'
                          : 'text-[var(--jarvis-muted)] hover:bg-[var(--jarvis-bg)] hover:text-[var(--jarvis-text)]'
                      }`}
                    >
                      {/* Fixed width container to align dot perfectly with top icons */}
                      <div className='w-5 flex justify-center flex-shrink-0'>
                        <div
                          className={`w-2 h-2 rounded-full ${
                            filter.key === 'all'
                              ? 'bg-[var(--jarvis-border-strong)]'
                              : filter.key === 'enabled'
                                ? 'bg-[var(--jarvis-success-text)]'
                                : filter.key === 'disabled'
                                  ? 'bg-[var(--jarvis-muted)]'
                                  : 'bg-[var(--jarvis-danger-text)]'
                          }`}
                        />
                      </div>
                      <div className='flex flex-1 items-center justify-between ml-3'>
                        <span className='whitespace-nowrap'>{filter.label}</span>
                        <span className={`text-xs px-2 py-0.5 rounded-full ${filter.colorClass}`}>{count}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Bottom User Profile Section */}
          <div className='p-3 border-t border-[color:var(--jarvis-border)] mt-auto'>
            {user && (
              <Menu as='div' className='relative'>
                {({ open }) => (
                  <>
                    <Menu.Button
                      className={`w-full flex items-center rounded-lg p-1.5 transition-all focus:outline-none border ${
                        open
                          ? 'bg-[var(--jarvis-card-muted)] border-[color:var(--jarvis-border-strong)]'
                          : 'border-transparent hover:bg-[var(--jarvis-card-muted)]'
                      }`}
                      title={user.username}
                    >
                      <div className='h-7 w-7 rounded-full bg-[var(--jarvis-primary-soft)] border border-[color:var(--jarvis-primary-soft-hover)] flex items-center justify-center text-[var(--jarvis-primary-text)] font-semibold flex-shrink-0 text-xs shadow-sm'>
                        {getFirstName().charAt(0).toUpperCase()}
                      </div>
                      <div
                        className={`flex flex-1 items-center justify-between overflow-hidden transition-all duration-300 ${
                          !sidebarOpen ? 'max-w-0 opacity-0 ml-0' : 'max-w-xs opacity-100 ml-3'
                        }`}
                      >
                        <div className='flex-1 min-w-0 text-left overflow-hidden'>
                          <div className='text-sm font-medium text-[var(--jarvis-text-strong)] truncate'>
                            {getFirstName()}
                          </div>
                          <div className='text-xs text-[var(--jarvis-muted)] truncate'>
                            {getUserEmail() || (user.isAdmin ? 'Admin Access' : 'User Access')}
                          </div>
                        </div>
                        <ChevronUpDownIcon className='ml-2 h-4 w-4 text-[var(--jarvis-muted)] flex-shrink-0' />
                      </div>
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
                      <Menu.Items
                        className={`absolute bottom-full mb-2 z-50 origin-bottom rounded-md bg-[var(--jarvis-card)] py-1 shadow-lg ring-1 ring-[color:var(--jarvis-border)] focus:outline-none ${
                          sidebarOpen ? 'w-full left-0' : 'w-48 left-12'
                        }`}
                      >
                        <Menu.Item>
                          {({ active }) => (
                            <Link
                              to='/generate-token'
                              className={`${
                                active
                                  ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                                  : 'text-[var(--jarvis-text)]'
                              } flex items-center px-4 py-2 text-sm`}
                            >
                              <KeyIcon className='mr-3 h-4 w-4 text-[var(--jarvis-muted)] flex-shrink-0' />
                              Generate Token
                            </Link>
                          )}
                        </Menu.Item>

                        <Menu.Item>
                          {({ active }) => (
                            <a
                              href={`https://ascendingdc.com/about/contact-us?m=Inquiry%3A+Additional+help+from+Jarvis&p=%2Findustry%2Fai&firstname=${encodeURIComponent(
                                getFirstName(),
                              )}&email=${encodeURIComponent(getUserEmail())}`}
                              target='_blank'
                              rel='noopener noreferrer'
                              className={`${
                                active
                                  ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                                  : 'text-[var(--jarvis-text)]'
                              } flex items-center px-4 py-2 text-sm`}
                            >
                              <QuestionMarkCircleIcon className='mr-3 h-4 w-4 text-[var(--jarvis-muted)] flex-shrink-0' />
                              Help & FAQ
                            </a>
                          )}
                        </Menu.Item>

                        <div className='my-1 border-t border-[color:var(--jarvis-border)]' />

                        <Menu.Item>
                          {({ active }) => (
                            <button
                              onClick={handleLogout}
                              className={`${
                                active
                                  ? 'bg-[var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)]'
                                  : 'text-[var(--jarvis-danger-text)]'
                              } flex w-full items-center px-4 py-2 text-sm`}
                            >
                              <ArrowRightStartOnRectangleIcon className='mr-3 h-4 w-4 text-[var(--jarvis-danger-text)] flex-shrink-0' />
                              Sign out
                            </button>
                          )}
                        </Menu.Item>
                      </Menu.Items>
                    </Transition>
                  </>
                )}
              </Menu>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default Content;
