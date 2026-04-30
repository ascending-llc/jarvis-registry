import { GlobeAltIcon, QueueListIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useSearchParams } from 'react-router-dom';

import AgentIcon from '@/assets/AgentIcon';
import McpIcon from '@/assets/McpIcon';
import { useServer } from '@/contexts/ServerContext';

interface NavMenuProps {
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
}

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

const NavMenu: React.FC<NavMenuProps> = ({ sidebarOpen, setSidebarOpen }) => {
  const [, setSearchParams] = useSearchParams();
  const { stats, agentStats, federationStats, viewMode, setViewMode, activeFilter, setActiveFilter } = useServer();

  const handleNavigation = (mode: 'servers' | 'agents' | 'workflow' | 'external') => {
    if (viewMode === mode) {
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

  return (
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
  );
};

export default NavMenu;
