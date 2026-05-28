import { useMemo, useState } from 'react';
import { useServer } from '@/contexts/ServerContext';
import type { LogicStep, NodePickerProps, PickerItem } from '../types';

/** Logic step configurations for NodePicker. */
const LOGIC_STEPS: LogicStep[] = [
  {
    id: 'gate',
    label: 'Approval Gate',
    desc: 'Pause run for human review',
    icon: '⏸',
    color: 'var(--jarvis-warning)',
    accent: 'var(--jarvis-warning-soft)',
  },
  {
    id: 'cond',
    label: 'Conditional',
    desc: 'If / else — two branch',
    icon: 'if',
    color: 'var(--jarvis-blue)',
    accent: 'var(--jarvis-blue-soft)',
    iconStyle: { fontStyle: 'italic', fontSize: 11 },
  },
  {
    id: 'parallel',
    label: 'Parallel',
    desc: 'Fan-out — unlimited branches',
    icon: '∥',
    color: 'var(--jarvis-teal)',
    accent: 'var(--jarvis-teal-soft)',
  },
  {
    id: 'router',
    label: 'Router',
    desc: 'Switch / case routing',
    icon: '⇄',
    color: 'var(--jarvis-pink)',
    accent: 'var(--jarvis-pink-soft)',
  },
  {
    id: 'loop',
    label: 'Loop',
    desc: 'Repeat with exit condition',
    icon: '↻',
    color: 'var(--jarvis-orange)',
    accent: 'rgba(251,146,60,.12)',
  },
  {
    id: 'pool',
    label: 'Agent Pool',
    desc: 'LLM delegates to up to 5 agents',
    icon: '◈',
    color: 'var(--jarvis-primary-hover)',
    accent: 'rgba(168,85,247,.15)',
  },
];

const TABS = ['A2A Agents', 'MCP Servers', 'Logic Step', 'All'] as const;
type TabType = (typeof TABS)[number];

/** Status color mapping. */
const STATUS_COLOR: Record<string, string> = {
  active: 'var(--jarvis-success)',
  inactive: 'var(--jarvis-warning)',
  error: 'var(--jarvis-danger)',
};
const STATUS_LABEL: Record<string, string> = { active: 'Active', inactive: 'Inactive', error: 'Error' };

/** Get 2-char initials from any display name. */
const getInitials = (name: string): string => {
  if (!name) return '??';
  const words = name.trim().split(/[\s\-_]+/);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
};

const NodePicker: React.FC<NodePickerProps> = ({ onPick, onClose, agentOnly = false, tab, onTabChange }) => {
  const { agents, servers, agentLoading, serverLoading } = useServer();
  const [internalTab, setInternalTab] = useState<TabType>('A2A Agents');
  const [query, setQuery] = useState('');
  const [hovered, setHovered] = useState<string | null>(null);

  const activeTab: string = tab ?? internalTab;
  const setActiveTab = (t: string) => {
    if (onTabChange) {
      onTabChange(t);
    } else if (TABS.includes(t as TabType)) {
      setInternalTab(t as TabType);
    }
  };

  const AGENTS: PickerItem[] = useMemo(
    () =>
      agents.map(a => ({
        id: a.id,
        label: a.name,
        desc: a.description || `${a.numSkills} skill${a.numSkills !== 1 ? 's' : ''}`,
        status: a.status || 'active',
      })),
    [agents],
  );

  const MCP_SERVERS: PickerItem[] = useMemo(
    () =>
      servers.map(s => ({
        id: s.id,
        label: s.name,
        desc: s.description || `${s.numTools ?? 0} tool${s.numTools !== 1 ? 's' : ''}`,
        status: s.status || 'active',
      })),
    [servers],
  );

  const q = query.toLowerCase();
  const match = (item: PickerItem | LogicStep): boolean =>
    !q || item.label.toLowerCase().includes(q) || item.desc.toLowerCase().includes(q);

  const showAgents = agentOnly || activeTab === 'All' || activeTab === 'A2A Agents';
  const showMcp = !agentOnly && (activeTab === 'All' || activeTab === 'MCP Servers');
  const showLogic = !agentOnly && (activeTab === 'All' || activeTab === 'Logic Step');

  const agentList: PickerItem[] = showAgents ? AGENTS.filter(match) : [];
  const mcpList: PickerItem[] = showMcp ? MCP_SERVERS.filter(match) : [];
  const logicList: LogicStep[] = showLogic ? LOGIC_STEPS.filter(match) : [];
  const noResults = agentList.length === 0 && mcpList.length === 0 && logicList.length === 0;
  const isLoading = (showAgents && agentLoading) || (showMcp && serverLoading);

  const activeTabs: readonly string[] = agentOnly ? ['A2A Agents'] : TABS;

  return (
    <div
      className='fixed inset-0 z-[100] flex items-center justify-center bg-[rgba(0,0,0,.62)] backdrop-blur-sm'
      onClick={onClose}
    >
      <div
        className='animate-pin bg-[var(--jarvis-card)] border border-[var(--jarvis-border-strong)] rounded-[14px] w-[500px] max-h-[560px] flex flex-col shadow-[0_32px_80px_-16px_rgba(0,0,0,.8)]'
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className='pt-[18px] px-5'>
          <div className='text-[15px] font-semibold text-[var(--jarvis-text-strong)] mb-0.5'>
            {agentOnly ? 'Select agent' : 'Add next node'}
          </div>
          <div className='text-xs text-[var(--jarvis-subtle)]'>
            {agentOnly
              ? 'Choose an A2A agent from the registry'
              : 'Choose from A2A agents, MCP servers, or logic steps'}
          </div>
        </div>

        {/* Search */}
        <div className='mt-3.5 mx-3.5 relative'>
          <svg
            className='absolute left-3 top-1/2 -translate-y-1/2 text-[var(--jarvis-subtle)] w-[15px] h-[15px] pointer-events-none'
            fill='none'
            stroke='currentColor'
            viewBox='0 0 24 24'
          >
            <circle cx='11' cy='11' r='8' strokeWidth='2' />
            <path strokeLinecap='round' strokeLinejoin='round' strokeWidth='2' d='M21 21l-4.35-4.35' />
          </svg>
          <input
            className='w-full bg-[var(--jarvis-surface)] border border-[var(--jarvis-border-strong)] rounded-lg text-[var(--jarvis-text-strong)] font-sans text-xs px-3.5 py-2.5 pl-9 outline-none'
            placeholder='Search nodes…'
            value={query}
            onChange={e => setQuery(e.target.value)}
            autoFocus
          />
        </div>

        {/* Tabs */}
        <div className='flex gap-0.5 pt-3 px-3.5 border-b border-[var(--jarvis-border)]'>
          {activeTabs.map(t => (
            <button
              key={t}
              className={`px-3 py-1.5 rounded-t-md font-sans text-xs font-medium cursor-pointer bg-none border-none transition-colors ${
                activeTab === t
                  ? 'text-[var(--jarvis-primary-text)] border-b-2 border-[var(--jarvis-primary-hover)]'
                  : 'text-[var(--jarvis-subtle)] border-b-2 border-transparent'
              }`}
              onClick={() => setActiveTab(t)}
            >
              {t}
            </button>
          ))}
        </div>

        {/* List */}
        <div className='flex-1 overflow-y-auto p-2'>
          {isLoading && <div className='text-center py-7 text-xs text-[var(--jarvis-subtle)]'>Loading…</div>}

          {!isLoading && agentList.length > 0 && (
            <>
              <div className='font-mono text-[10px] font-bold tracking-[0.08em] uppercase text-[var(--jarvis-subtle)] px-2.5 py-2 pb-1'>
                A2A Agents
              </div>
              {agentList.map(a => (
                <button
                  key={a.id}
                  className={`w-full text-left flex items-center gap-2.5 p-2.5 rounded-lg cursor-pointer border-none outline-none focus:ring-1 focus:ring-[var(--jarvis-primary)] transition-colors ${
                    hovered === a.id ? 'bg-[var(--jarvis-surface)]' : 'bg-transparent'
                  }`}
                  onMouseEnter={() => setHovered(a.id)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => onPick('agent', a)}
                >
                  <div className='w-8 h-8 rounded-lg flex items-center justify-center font-sans font-bold text-[11px] bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)] shrink-0'>
                    {getInitials(a.label)}
                  </div>
                  <div className='flex-1 min-w-0'>
                    <div className='text-[13px] font-medium text-[var(--jarvis-text-strong)]'>{a.label}</div>
                    <div className='text-[11px] text-[var(--jarvis-subtle)] mt-0.5'>{a.desc}</div>
                  </div>
                  <div className='ml-auto text-[11px] text-[var(--jarvis-subtle)] whitespace-nowrap flex items-center gap-1 shrink-0'>
                    <div
                      className='w-[5px] h-[5px] rounded-full'
                      style={{ background: STATUS_COLOR[a.status ?? 'active'] || 'var(--jarvis-subtle)' }}
                    />
                    {STATUS_LABEL[a.status ?? 'active'] || 'Unknown'}
                  </div>
                </button>
              ))}
            </>
          )}

          {!isLoading && mcpList.length > 0 && (
            <>
              <div className='font-mono text-[10px] font-bold tracking-[0.08em] uppercase text-[var(--jarvis-subtle)] px-2.5 py-2 pb-1'>
                MCP Servers
              </div>
              {mcpList.map(m => (
                <button
                  key={m.id}
                  className={`w-full text-left flex items-center gap-2.5 p-2.5 rounded-lg cursor-pointer border-none outline-none focus:ring-1 focus:ring-[var(--jarvis-primary)] transition-colors ${
                    hovered === m.id ? 'bg-[var(--jarvis-surface)]' : 'bg-transparent'
                  }`}
                  onMouseEnter={() => setHovered(m.id)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => onPick('mcp', m)}
                >
                  <div className='w-8 h-8 rounded-lg flex items-center justify-center font-sans font-bold text-[11px] bg-[var(--jarvis-blue-soft)] text-[var(--jarvis-blue)] shrink-0'>
                    {getInitials(m.label)}
                  </div>
                  <div className='flex-1 min-w-0'>
                    <div className='text-[13px] font-medium text-[var(--jarvis-text-strong)]'>{m.label}</div>
                    <div className='text-[11px] text-[var(--jarvis-subtle)] mt-0.5'>{m.desc}</div>
                  </div>
                  <div className='ml-auto text-[11px] text-[var(--jarvis-subtle)] whitespace-nowrap flex items-center gap-1 shrink-0'>
                    <div
                      className='w-[5px] h-[5px] rounded-full'
                      style={{ background: STATUS_COLOR[m.status ?? 'active'] || 'var(--jarvis-subtle)' }}
                    />
                    {STATUS_LABEL[m.status ?? 'active'] || 'Unknown'}
                  </div>
                </button>
              ))}
            </>
          )}

          {!isLoading && logicList.length > 0 && (
            <>
              <div className='font-mono text-[10px] font-bold tracking-[0.08em] uppercase text-[var(--jarvis-subtle)] px-2.5 py-2 pb-1'>
                Logic Step
              </div>
              {logicList.map(l => (
                <button
                  key={l.id}
                  className={`w-full text-left flex items-center gap-2.5 p-2.5 rounded-lg cursor-pointer border-none outline-none focus:ring-1 focus:ring-[var(--jarvis-primary)] transition-colors ${
                    hovered === l.id ? 'bg-[var(--jarvis-surface)]' : 'bg-transparent'
                  }`}
                  onMouseEnter={() => setHovered(l.id)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => onPick('logic', l)}
                >
                  <div
                    className='w-8 h-8 rounded-lg flex items-center justify-center font-sans font-bold text-[11px] shrink-0'
                    style={{ background: l.accent, color: l.color, ...l.iconStyle }}
                  >
                    {l.icon}
                  </div>
                  <div className='flex-1 min-w-0'>
                    <div className='text-[13px] font-medium' style={{ color: l.color }}>
                      {l.label}
                    </div>
                    <div className='text-[11px] text-[var(--jarvis-subtle)] mt-0.5'>{l.desc}</div>
                  </div>
                </button>
              ))}
            </>
          )}

          {!isLoading && noResults && (
            <div className='text-center py-7 text-xs text-[var(--jarvis-subtle)]'>
              {query ? `No results for "${query}"` : 'No items found in registry'}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className='px-4 py-3 border-t border-[var(--jarvis-border)] flex justify-end'>
          <button
            className='bg-transparent border border-[var(--jarvis-border)] rounded-lg text-[var(--jarvis-text)] px-3 py-1.5 font-sans text-xs font-medium cursor-pointer'
            onClick={onClose}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
};

export default NodePicker;
