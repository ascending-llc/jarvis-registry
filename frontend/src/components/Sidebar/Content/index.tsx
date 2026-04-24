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
  const { stats, agentStats, federationStats, viewMode, activeFilter, setActiveFilter } = useServer();

  const [showScopes, setShowScopes] = useState(false);

  const isTokenPage = location.pathname === '/generate-token';
  const isServerRegistryOrEditPage = location.pathname === '/server-registry' || location.pathname === '/server-edit';
  const isAgentRegistryOrEditPage = location.pathname === '/agent-registry' || location.pathname === '/agent-edit';
  const isFederationRegistryOrEditPage =
    location.pathname === '/federation-registry' || location.pathname === '/federation-edit';

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
    <div className="flex h-full flex-col">
      {/* Conditional Content */}
      {isServerRegistryOrEditPage || isAgentRegistryOrEditPage || isFederationRegistryOrEditPage ? (
        <div className="flex-1 p-4 md:p-6">
          {/* Navigation Links */}
          <div className="space-y-2 mb-6">
            <Link
              to='/'
              className="flex items-center space-x-3 px-3 py-2 rounded-lg text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] text-[var(--jarvis-text)] text-[var(--jarvis-text)] hover:bg-[var(--jarvis-card-muted)] hover:bg-[var(--jarvis-card-muted)]"
              onClick={() => window.innerWidth < 768 && setSidebarOpen(false)} // Only close on mobile
              tabIndex={0}
            >
              <ArrowLeftIcon className="h-4 w-4" />
              <span>{isServerRegistryOrEditPage ? 'Back to MCP' : 'Back to Dashboard'}</span>
            </Link>
          </div>
        </div>
      ) : isTokenPage ? (
        /* Token Page - Show navigation and user info */
        <div className="flex-1 p-4 md:p-6">
          {/* Navigation Links */}
          <div className="space-y-2 mb-6">
            <Link
              to='/'
              className="flex items-center space-x-3 px-3 py-2 rounded-lg text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] text-[var(--jarvis-text)] text-[var(--jarvis-text)] hover:bg-[var(--jarvis-card-muted)] hover:bg-[var(--jarvis-card-muted)]"
              onClick={() => window.innerWidth < 768 && setSidebarOpen(false)} // Only close on mobile
              tabIndex={0}
            >
              <ArrowLeftIcon className="h-4 w-4" />
              <span>Back to Dashboard</span>
            </Link>
          </div>

          {/* User Access Information */}
          {user && (
            <div className="p-3 bg-[var(--jarvis-bg)] bg-[var(--jarvis-card)] rounded-lg mb-6">
              <div className="text-sm">
                <div className="font-medium text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)] mb-1">{user.username}</div>
                <div className="text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-text)] mb-2">
                  {user.isAdmin ? (
                    <span className="text-[var(--jarvis-success-text)] text-[var(--jarvis-success-text)]">🔑 Admin Access</span>
                  ) : user.canModifyServers ? (
                    <span className="text-[var(--jarvis-info-text)] text-[var(--jarvis-info-text)]">⚙️ Modify Access</span>
                  ) : (
                    <span className="text-[var(--jarvis-muted)] text-[var(--jarvis-text)]">👁️ Read-only Access</span>
                  )}
                  {user.authMethod === 'oauth2' && user.provider && <span className="ml-1">({user.provider})</span>}
                </div>

                {/* Scopes toggle */}
                {!user.isAdmin && user.scopes && user.scopes.length > 0 && (
                  <div>
                    <button
                      onClick={() => setShowScopes(!showScopes)}
                      className="flex items-center justify-between w-full text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-text)] hover:text-[var(--jarvis-text)] hover:text-[var(--jarvis-icon-hover)] transition-colors py-1"
                    >
                      <span>Scopes ({user.scopes.length})</span>
                      {showScopes ? <ChevronUpIcon className="h-3 w-3" /> : <ChevronDownIcon className="h-3 w-3" />}
                    </button>

                    {showScopes && (
                      <div className="mt-2 space-y-2 max-h-32 overflow-y-auto">
                        {user.scopes.map((scope: string) => (
                          <div key={scope} className="bg-[var(--jarvis-info-soft)] bg-[var(--jarvis-info-soft)] p-2 rounded text-xs">
                            <div className="font-medium text-[var(--jarvis-info-text)] text-[var(--jarvis-info-text)]">{scope}</div>
                            <div className="text-[var(--jarvis-info-text)] mt-1">{getScopeDescription(scope)}</div>
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
          <div className="text-center">
            <KeyIcon className="h-12 w-12 text-[var(--jarvis-primary)] mx-auto mb-4" />
            <h3 className="text-lg font-medium text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)] mb-2">Token Generation</h3>
            <p className="text-sm text-[var(--jarvis-muted)] text-[var(--jarvis-muted)] mb-4">
              Create personal access tokens for programmatic access to MCP servers
            </p>
            <div className="text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-muted)] space-y-1">
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
          <div className="p-4 md:p-6 border-b border-[color:var(--jarvis-border)] border-[color:var(--jarvis-border)]">
            {/* User Access Information */}
            {user && (
              <div className="p-3 bg-[var(--jarvis-bg)] bg-[var(--jarvis-card)] rounded-lg">
                <div className="text-sm">
                  <div className="font-medium text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)] mb-1">{user.username}</div>
                  <div className="text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-text)] mb-2">
                    {user.isAdmin ? (
                      <span className="text-[var(--jarvis-success-text)] text-[var(--jarvis-success-text)]">🔑 Admin Access</span>
                    ) : user.canModifyServers ? (
                      <span className="text-[var(--jarvis-info-text)] text-[var(--jarvis-info-text)]">⚙️ Modify Access</span>
                    ) : (
                      <span className="text-[var(--jarvis-muted)] text-[var(--jarvis-text)]">👁️ Read-only Access</span>
                    )}
                    {user.authMethod === 'oauth2' && user.provider && <span className="ml-1">({user.provider})</span>}
                  </div>

                  {/* Scopes toggle */}
                  {!user.isAdmin && user.scopes && user.scopes.length > 0 && (
                    <div>
                      <button
                        onClick={() => setShowScopes(!showScopes)}
                        className="flex items-center justify-between w-full text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-text)] hover:text-[var(--jarvis-text)] hover:text-[var(--jarvis-icon-hover)] transition-colors py-1"
                      >
                        <span>Scopes ({user.scopes.length})</span>
                        {showScopes ? <ChevronUpIcon className="h-3 w-3" /> : <ChevronDownIcon className="h-3 w-3" />}
                      </button>

                      {showScopes && (
                        <div className="mt-2 space-y-2 max-h-32 overflow-y-auto">
                          {user.scopes.map((scope: string) => (
                            <div key={scope} className="bg-[var(--jarvis-info-soft)] bg-[var(--jarvis-info-soft)] p-2 rounded text-xs">
                              <div className="font-medium text-[var(--jarvis-info-text)] text-[var(--jarvis-info-text)]">{scope}</div>
                              <div className="text-[var(--jarvis-info-text)] mt-1">{getScopeDescription(scope)}</div>
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
          <div className="flex-1 p-4 md:p-6">
            <div className="flex items-center space-x-2 mb-4">
              <FunnelIcon className="h-4 w-4 text-[var(--jarvis-muted)] text-[var(--jarvis-muted)]" />
              <h3 className="text-sm font-medium text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)]">Filter by Status</h3>
            </div>

            <div className="space-y-2">
              {filters.map((filter: any) => {
                // Calculate count based on view mode
                let count = 0;
                if (viewMode === 'servers') {
                  count = stats[filter.count as keyof typeof stats];
                } else if (viewMode === 'agents') {
                  count = agentStats[filter.count as keyof typeof agentStats];
                } else if (viewMode === 'external' && federationStats) {
                  count = federationStats[filter.count as keyof typeof federationStats] || 0;
                }

                return (
                  <button
                    key={filter.key}
                    onClick={() => setActiveFilter(filter.key)}
                    className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] ${
 activeFilter === filter.key
 ? 'bg-[var(--jarvis-primary-soft)] bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)] dark:text-[var(--jarvis-primary-text)] border border-[color:var(--jarvis-primary-soft)] dark:border-[color:var(--jarvis-primary-soft)]'
 : 'text-[var(--jarvis-text)] text-[var(--jarvis-text)] hover:bg-[var(--jarvis-card-muted)] hover:bg-[var(--jarvis-card)]'
 }`}
                    tabIndex={0}
                  >
                    <div className="flex items-center justify-between">
                      <span>{filter.label}</span>
                      <span className="text-xs bg-[var(--jarvis-card-muted)] bg-[var(--jarvis-card-muted)] px-2 py-1 rounded-full">{count}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Statistics Section */}
          <div className="border-t border-[color:var(--jarvis-border)] border-[color:var(--jarvis-border)] p-4 md:p-6">
            <div className="flex items-center space-x-2 mb-4">
              <ChartBarIcon className="h-5 w-5 text-[var(--jarvis-muted)]" />
              <h3 className="text-sm font-medium text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)]">Server Statistics</h3>
            </div>

            <div className="grid grid-cols-2 gap-3 mb-6">
              <div className="text-center p-3 bg-[var(--jarvis-bg)] bg-[var(--jarvis-card)] rounded-lg">
                <div className="text-xl font-semibold text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)]">{stats.total}</div>
                <div className="text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-text)]">Total</div>
              </div>
              <div className="text-center p-3 bg-[var(--jarvis-success-soft)] bg-[var(--jarvis-success-soft)] rounded-lg">
                <div className="text-xl font-semibold text-[var(--jarvis-success-text)] text-[var(--jarvis-success-text)]">{stats.enabled}</div>
                <div className="text-xs text-[var(--jarvis-success-text)] text-[var(--jarvis-success-text)]">Enabled</div>
              </div>
              <div className="text-center p-3 bg-[var(--jarvis-bg)] bg-[var(--jarvis-card)] rounded-lg">
                <div className="text-xl font-semibold text-[var(--jarvis-muted)] text-[var(--jarvis-text)]">{stats.disabled}</div>
                <div className="text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-text)]">Disabled</div>
              </div>
              <div className="text-center p-3 bg-[var(--jarvis-danger-soft)] bg-[var(--jarvis-danger-soft)] rounded-lg">
                <div className="text-xl font-semibold text-[var(--jarvis-danger-text)] text-[var(--jarvis-danger-text)]">{stats.withIssues}</div>
                <div className="text-xs text-[var(--jarvis-danger-text)] text-[var(--jarvis-danger-text)]">Issues</div>
              </div>
            </div>

            <div className="flex items-center space-x-2 mb-4">
              <ChartBarIcon className="h-5 w-5 text-[var(--jarvis-muted)]" />
              <h3 className="text-sm font-medium text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)]">Agent Statistics</h3>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="text-center p-3 bg-[var(--jarvis-bg)] bg-[var(--jarvis-card)] rounded-lg">
                <div className="text-xl font-semibold text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)]">{agentStats.total}</div>
                <div className="text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-text)]">Total</div>
              </div>
              <div className="text-center p-3 bg-[var(--jarvis-success-soft)] bg-[var(--jarvis-success-soft)] rounded-lg">
                <div className="text-xl font-semibold text-[var(--jarvis-success-text)] text-[var(--jarvis-success-text)]">{agentStats.enabled}</div>
                <div className="text-xs text-[var(--jarvis-success-text)] text-[var(--jarvis-success-text)]">Enabled</div>
              </div>
              <div className="text-center p-3 bg-[var(--jarvis-bg)] bg-[var(--jarvis-card)] rounded-lg">
                <div className="text-xl font-semibold text-[var(--jarvis-muted)] text-[var(--jarvis-text)]">{agentStats.disabled}</div>
                <div className="text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-text)]">Disabled</div>
              </div>
              <div className="text-center p-3 bg-[var(--jarvis-danger-soft)] bg-[var(--jarvis-danger-soft)] rounded-lg">
                <div className="text-xl font-semibold text-[var(--jarvis-danger-text)] text-[var(--jarvis-danger-text)]">{agentStats.withIssues}</div>
                <div className="text-xs text-[var(--jarvis-danger-text)] text-[var(--jarvis-danger-text)]">Issues</div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default Content;
