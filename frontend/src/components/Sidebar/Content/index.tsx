import {
  ArrowLeftIcon,
  ChartBarIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  FunnelIcon,
  KeyIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { useAuth } from '@/contexts/AuthContext';
import { useServer } from '@/contexts/ServerContext';

const Content: React.FC<any> = ({ setSidebarOpen }) => {
  const location = useLocation();
  const { user } = useAuth();
  const { stats, agentStats, viewMode, activeFilter, setActiveFilter } = useServer();

  const [showScopes, setShowScopes] = useState(false);

  const isTokenPage = location.pathname === '/generate-token';
  const isServerRegistryOrEditPage = location.pathname === '/server-registry' || location.pathname === '/server-edit';

  /** List of filters available for token generation */
  const filters = [
    { key: 'all', label: 'All', count: 'total' },
    { key: 'enabled', label: 'Enabled', count: 'enabled' },
    { key: 'disabled', label: 'Disabled', count: 'disabled' },
    { key: 'unhealthy', label: 'With Issues', count: 'withIssues' },
  ];

  /** Scope descriptions mapping */
  const getScopeDescription = (scope: string) => {
    const scopeMappings: { [key: string]: string } = {
      'mcp-servers-restricted/read': 'Read access to restricted MCP servers',
      'mcp-servers/read': 'Read access to all MCP servers',
      'mcp-servers/write': 'Write access to MCP servers',
      'mcp-registry-user': 'Basic registry user permissions',
      'mcp-registry-admin': 'Full registry administration access',
      'health-check': 'Health check and monitoring access',
      'token-generation': 'Ability to generate access tokens',
      'server-management': 'Manage server configurations',
    };
    return scopeMappings[scope] || 'Custom permission scope';
  };

  return (
    <div className='flex h-full flex-col'>
      {/* Conditional Content */}
      {isServerRegistryOrEditPage ? (
        <div className='flex-1 p-4 md:p-6'>
          {/* Navigation Links */}
          <div className='space-y-2 mb-6'>
            <Link
              to='/'
              className='flex items-center space-x-3 px-3 py-2 rounded-lg text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-purple-500 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'
              onClick={() => window.innerWidth < 768 && setSidebarOpen(false)} // Only close on mobile
              tabIndex={0}
            >
              <ArrowLeftIcon className='h-4 w-4' />
              <span>Back to MCP</span>
            </Link>
          </div>
        </div>
      ) : isTokenPage ? (
        /* Token Page - Show navigation and user info */
        <div className='flex-1 p-4 md:p-6'>
          {/* Navigation Links */}
          <div className='space-y-2 mb-6'>
            <Link
              to='/'
              className='flex items-center space-x-3 px-3 py-2 rounded-lg text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-purple-500 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'
              onClick={() => window.innerWidth < 768 && setSidebarOpen(false)} // Only close on mobile
              tabIndex={0}
            >
              <ArrowLeftIcon className='h-4 w-4' />
              <span>Back to Dashboard</span>
            </Link>
          </div>

          {/* User Access Information */}
          {user && (
            <div className='p-3 bg-gray-50 dark:bg-gray-800 rounded-lg mb-6'>
              <div className='text-sm'>
                <div className='font-medium text-gray-900 dark:text-white mb-1'>{user.username}</div>
                <div className='text-xs text-gray-600 dark:text-gray-300 mb-2'>
                  {user.isAdmin ? (
                    <span className='text-green-600 dark:text-green-400'>🔑 Admin Access</span>
                  ) : user.canModifyServers ? (
                    <span className='text-blue-600 dark:text-blue-400'>⚙️ Modify Access</span>
                  ) : (
                    <span className='text-gray-600 dark:text-gray-300'>👁️ Read-only Access</span>
                  )}
                  {user.authMethod === 'oauth2' && user.provider && <span className='ml-1'>({user.provider})</span>}
                </div>

                {/* Scopes toggle */}
                {!user.isAdmin && user.scopes && user.scopes.length > 0 && (
                  <div>
                    <button
                      onClick={() => setShowScopes(!showScopes)}
                      className='flex items-center justify-between w-full text-xs text-gray-500 dark:text-gray-300 hover:text-gray-700 dark:hover:text-gray-100 transition-colors py-1'
                    >
                      <span>Scopes ({user.scopes.length})</span>
                      {showScopes ? <ChevronUpIcon className='h-3 w-3' /> : <ChevronDownIcon className='h-3 w-3' />}
                    </button>

                    {showScopes && (
                      <div className='mt-2 space-y-2 max-h-32 overflow-y-auto'>
                        {user.scopes.map((scope: string) => (
                          <div key={scope} className='bg-blue-50 dark:bg-blue-900/20 p-2 rounded text-xs'>
                            <div className='font-medium text-blue-800 dark:text-blue-200'>{scope}</div>
                            <div className='text-blue-600 dark:text-blue-300 mt-1'>{getScopeDescription(scope)}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Token Generation Help */}
          <div className='text-center'>
            <KeyIcon className='h-12 w-12 text-purple-600 mx-auto mb-4' />
            <h3 className='text-lg font-medium text-gray-900 dark:text-white mb-2'>Token Generation</h3>
            <p className='text-sm text-gray-600 dark:text-gray-400 mb-4'>
              Create personal access tokens for programmatic access to MCP servers
            </p>
            <div className='text-xs text-gray-500 dark:text-gray-400 space-y-1'>
              <p>• Tokens inherit your current permissions</p>
              <p>• Configure expiration time and scopes</p>
              <p>• Use tokens for programmatic access</p>
            </div>
          </div>
        </div>
      ) : (
        /* Dashboard - Show user info, filters and stats */
        <>
          {/* User Info Header */}
          <div className='p-4 md:p-6 border-b border-gray-200 dark:border-gray-700'>
            {/* User Access Information */}
            {user && (
              <div className='p-3 bg-gray-50 dark:bg-gray-800 rounded-lg'>
                <div className='text-sm'>
                  <div className='font-medium text-gray-900 dark:text-white mb-1'>{user.username}</div>
                  <div className='text-xs text-gray-600 dark:text-gray-300 mb-2'>
                    {user.isAdmin ? (
                      <span className='text-green-600 dark:text-green-400'>🔑 Admin Access</span>
                    ) : user.canModifyServers ? (
                      <span className='text-blue-600 dark:text-blue-400'>⚙️ Modify Access</span>
                    ) : (
                      <span className='text-gray-600 dark:text-gray-300'>👁️ Read-only Access</span>
                    )}
                    {user.authMethod === 'oauth2' && user.provider && <span className='ml-1'>({user.provider})</span>}
                  </div>

                  {/* Scopes toggle */}
                  {!user.isAdmin && user.scopes && user.scopes.length > 0 && (
                    <div>
                      <button
                        onClick={() => setShowScopes(!showScopes)}
                        className='flex items-center justify-between w-full text-xs text-gray-500 dark:text-gray-300 hover:text-gray-700 dark:hover:text-gray-100 transition-colors py-1'
                      >
                        <span>Scopes ({user.scopes.length})</span>
                        {showScopes ? <ChevronUpIcon className='h-3 w-3' /> : <ChevronDownIcon className='h-3 w-3' />}
                      </button>

                      {showScopes && (
                        <div className='mt-2 space-y-2 max-h-32 overflow-y-auto'>
                          {user.scopes.map((scope: string) => (
                            <div key={scope} className='bg-blue-50 dark:bg-blue-900/20 p-2 rounded text-xs'>
                              <div className='font-medium text-blue-800 dark:text-blue-200'>{scope}</div>
                              <div className='text-blue-600 dark:text-blue-300 mt-1'>{getScopeDescription(scope)}</div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Filters Section */}
          <div className='flex-1 p-4 md:p-6'>
            <div className='flex items-center space-x-2 mb-4'>
              <FunnelIcon className='h-4 w-4 text-gray-600 dark:text-gray-400' />
              <h3 className='text-sm font-medium text-gray-900 dark:text-white'>Filter by Status</h3>
            </div>

            <div className='space-y-2'>
              {filters.map((filter: any) => {
                // Calculate count based on view mode
                let count = 0;
                if (viewMode === 'servers') {
                  count = stats[filter.count as keyof typeof stats];
                } else {
                  count = agentStats[filter.count as keyof typeof agentStats];
                }

                return (
                  <button
                    key={filter.key}
                    onClick={() => setActiveFilter(filter.key)}
                    className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-purple-500 ${
                      activeFilter === filter.key
                        ? 'bg-primary-100 dark:bg-primary-900 text-primary-700 dark:text-primary-300 border border-primary-200 dark:border-primary-800'
                        : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
                    }`}
                    tabIndex={0}
                  >
                    <div className='flex items-center justify-between'>
                      <span>{filter.label}</span>
                      <span className='text-xs bg-gray-200 dark:bg-gray-700 px-2 py-1 rounded-full'>{count}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Statistics Section */}
          <div className='border-t border-gray-200 dark:border-gray-700 p-4 md:p-6'>
            <div className='flex items-center space-x-2 mb-4'>
              <ChartBarIcon className='h-5 w-5 text-gray-500' />
              <h3 className='text-sm font-medium text-gray-900 dark:text-white'>Server Statistics</h3>
            </div>

            <div className='grid grid-cols-2 gap-3 mb-6'>
              <div className='text-center p-3 bg-gray-50 dark:bg-gray-800 rounded-lg'>
                <div className='text-xl font-semibold text-gray-900 dark:text-white'>{stats.total}</div>
                <div className='text-xs text-gray-500 dark:text-gray-300'>Total</div>
              </div>
              <div className='text-center p-3 bg-green-50 dark:bg-green-900/20 rounded-lg'>
                <div className='text-xl font-semibold text-green-600 dark:text-green-400'>{stats.enabled}</div>
                <div className='text-xs text-green-600 dark:text-green-400'>Enabled</div>
              </div>
              <div className='text-center p-3 bg-gray-50 dark:bg-gray-800 rounded-lg'>
                <div className='text-xl font-semibold text-gray-500 dark:text-gray-300'>{stats.disabled}</div>
                <div className='text-xs text-gray-500 dark:text-gray-300'>Disabled</div>
              </div>
              <div className='text-center p-3 bg-red-50 dark:bg-red-900/20 rounded-lg'>
                <div className='text-xl font-semibold text-red-600 dark:text-red-400'>{stats.withIssues}</div>
                <div className='text-xs text-red-600 dark:text-red-400'>Issues</div>
              </div>
            </div>

            <div className='flex items-center space-x-2 mb-4'>
              <ChartBarIcon className='h-5 w-5 text-gray-500' />
              <h3 className='text-sm font-medium text-gray-900 dark:text-white'>Agent Statistics</h3>
            </div>

            <div className='grid grid-cols-2 gap-3'>
              <div className='text-center p-3 bg-gray-50 dark:bg-gray-800 rounded-lg'>
                <div className='text-xl font-semibold text-gray-900 dark:text-white'>{agentStats.total}</div>
                <div className='text-xs text-gray-500 dark:text-gray-300'>Total</div>
              </div>
              <div className='text-center p-3 bg-green-50 dark:bg-green-900/20 rounded-lg'>
                <div className='text-xl font-semibold text-green-600 dark:text-green-400'>{agentStats.enabled}</div>
                <div className='text-xs text-green-600 dark:text-green-400'>Enabled</div>
              </div>
              <div className='text-center p-3 bg-gray-50 dark:bg-gray-800 rounded-lg'>
                <div className='text-xl font-semibold text-gray-500 dark:text-gray-300'>{agentStats.disabled}</div>
                <div className='text-xs text-gray-500 dark:text-gray-300'>Disabled</div>
              </div>
              <div className='text-center p-3 bg-red-50 dark:bg-red-900/20 rounded-lg'>
                <div className='text-xl font-semibold text-red-600 dark:text-red-400'>{agentStats.withIssues}</div>
                <div className='text-xs text-red-600 dark:text-red-400'>Issues</div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default Content;
