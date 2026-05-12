// @ts-nocheck
import { useMemo, useState } from 'react';
import { useServer } from '@/contexts/ServerContext';

const LOGIC_STEPS = [
  {
    id: 'gate',
    label: 'Approval Gate',
    desc: 'Pause run for human review',
    icon: '⏸',
    color: 'var(--wf-amber)',
    accent: 'var(--wf-atint)',
  },
  {
    id: 'cond',
    label: 'Conditional',
    desc: 'If / else — two branch',
    icon: 'if',
    color: 'var(--wf-blue)',
    accent: 'var(--wf-btint)',
    iconStyle: { fontStyle: 'italic', fontSize: 11 },
  },
  {
    id: 'parallel',
    label: 'Parallel',
    desc: 'Fan-out — unlimited branches',
    icon: '∥',
    color: 'var(--wf-teal)',
    accent: 'var(--wf-ttint)',
  },
  {
    id: 'router',
    label: 'Router',
    desc: 'Switch / case routing',
    icon: '⇄',
    color: 'var(--wf-pink)',
    accent: 'var(--wf-pktint)',
  },
  {
    id: 'loop',
    label: 'Loop',
    desc: 'Repeat with exit condition',
    icon: '↻',
    color: 'var(--wf-orange)',
    accent: 'rgba(251,146,60,.12)',
  },
  {
    id: 'pool',
    label: 'Agent Pool',
    desc: 'LLM delegates to up to 5 agents',
    icon: '◈',
    color: 'var(--wf-purple-2)',
    accent: 'rgba(168,85,247,.15)',
  },
];

const TABS = ['A2A Agents', 'MCP Servers', 'Logic Step', 'All'];

/* ── Styles (inline — keeps component self-contained) ── */
const S = {
  overlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,.62)',
    backdropFilter: 'blur(2px)',
    zIndex: 100,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  modal: {
    background: 'var(--wf-bg-2)',
    border: '1px solid var(--wf-border-strong)',
    borderRadius: 14,
    width: 500,
    maxHeight: 560,
    display: 'flex',
    flexDirection: 'column',
    boxShadow: '0 32px 80px -16px rgba(0,0,0,.8)',
    animation: 'pin .18s cubic-bezier(.34,1.3,.64,1) forwards',
  },
  head: { padding: '18px 20px 0' },
  title: { fontSize: 15, fontWeight: 600, color: 'var(--wf-text-1)', marginBottom: 2 },
  sub: { fontSize: 12, color: 'var(--wf-text-4)' },
  sWrap: { margin: '14px 14px 0', position: 'relative' },
  sInput: {
    width: '100%',
    background: 'var(--wf-bg-3)',
    border: '1px solid var(--wf-border-strong)',
    borderRadius: 9,
    color: 'var(--wf-text-1)',
    padding: '10px 14px 10px 36px',
    fontFamily: 'Inter,sans-serif',
    fontSize: 13,
    outline: 'none',
  },
  sIcon: {
    position: 'absolute',
    left: 12,
    top: '50%',
    transform: 'translateY(-50%)',
    color: 'var(--wf-text-4)',
    width: 15,
    height: 15,
    pointerEvents: 'none',
  },
  tabRow: { display: 'flex', gap: 2, padding: '12px 14px 0', borderBottom: '1px solid var(--wf-border)' },
  tab: on => ({
    padding: '7px 13px',
    borderRadius: '6px 6px 0 0',
    fontFamily: 'Inter,sans-serif',
    fontSize: 12,
    fontWeight: 500,
    color: on ? 'var(--wf-purple-3)' : 'var(--wf-text-4)',
    cursor: 'pointer',
    background: 'none',
    border: 'none',
    borderBottom: on ? '2px solid var(--wf-purple-2)' : '2px solid transparent',
    transition: 'color .15s',
  }),
  list: { flex: 1, overflowY: 'auto', padding: 8 },
  sec: {
    fontFamily: 'JetBrains Mono,monospace',
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '.08em',
    textTransform: 'uppercase',
    color: 'var(--wf-text-4)',
    padding: '8px 10px 4px',
  },
  item: (hov, borderColor) => ({
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: 10,
    paddingLeft: borderColor ? 8 : 10,
    borderRadius: 9,
    cursor: 'pointer',
    background: hov ? 'var(--wf-bg-3)' : 'transparent',
    borderLeft: borderColor ? `3px solid ${borderColor}` : 'none',
    transition: 'background .15s',
  }),
  icon: (bg, color, extra) => ({
    width: 32,
    height: 32,
    borderRadius: 8,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontFamily: 'Inter,sans-serif',
    fontWeight: 700,
    fontSize: 11,
    background: bg,
    color,
    flexShrink: 0,
    ...extra,
  }),
  name: color => ({ fontSize: 13, fontWeight: 500, color: color || 'var(--wf-text-1)' }),
  key: { fontSize: 11, color: 'var(--wf-text-4)', marginTop: 1 },
  stat: {
    marginLeft: 'auto',
    fontSize: 11,
    color: 'var(--wf-text-4)',
    whiteSpace: 'nowrap',
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    flexShrink: 0,
  },
  dot: { width: 5, height: 5, borderRadius: '50%', background: 'var(--wf-green)' },
  foot: { padding: '11px 15px', borderTop: '1px solid var(--wf-border)', display: 'flex', justifyContent: 'flex-end' },
  cancel: {
    background: 'transparent',
    border: '1px solid var(--wf-border)',
    borderRadius: 7,
    color: 'var(--wf-text-2)',
    padding: '6px 12px',
    fontFamily: 'Inter,sans-serif',
    fontSize: 12.5,
    fontWeight: 500,
    cursor: 'pointer',
  },
};

/* ── Map real status to dot color ── */
const STATUS_COLOR = {
  active: 'var(--wf-green)',
  inactive: 'var(--wf-amber)',
  error: 'var(--wf-red)',
};
const STATUS_LABEL = { active: 'Active', inactive: 'Inactive', error: 'Error' };

/* ── Get 2-char initials from any display name ── */
function getInitials(name) {
  if (!name) return '??';
  const words = name.trim().split(/[\s\-_]+/);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

export default function NodePicker({ onPick, onClose, agentOnly = false }) {
  const { agents, servers, agentLoading, serverLoading } = useServer();
  const [tab, setTab] = useState(agentOnly ? 'A2A Agents' : 'All');
  const [query, setQuery] = useState('');
  const [hovered, setHovered] = useState(null);

  /* ── Map real data to picker-item shape { id, label, desc, status } ── */
  const AGENTS = useMemo(
    () =>
      agents.map(a => ({
        id: a.id,
        label: a.name,
        desc: a.description || `${a.numSkills} skill${a.numSkills !== 1 ? 's' : ''}`,
        status: a.status || 'active',
      })),
    [agents],
  );

  const MCP_SERVERS = useMemo(
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
  const match = item => !q || item.label.toLowerCase().includes(q) || item.desc.toLowerCase().includes(q);

  const showAgents = agentOnly || tab === 'All' || tab === 'A2A Agents';
  const showMcp = !agentOnly && (tab === 'All' || tab === 'MCP Servers');
  const showLogic = !agentOnly && (tab === 'All' || tab === 'Logic Step');

  const agentList = showAgents ? AGENTS.filter(match) : [];
  const mcpList = showMcp ? MCP_SERVERS.filter(match) : [];
  const logicList = showLogic ? LOGIC_STEPS.filter(match) : [];
  const noResults = agentList.length === 0 && mcpList.length === 0 && logicList.length === 0;
  const isLoading = (showAgents && agentLoading) || (showMcp && serverLoading);

  const activeTabs = agentOnly ? ['A2A Agents'] : TABS;

  return (
    <div style={S.overlay} onClick={onClose}>
      <style>{`@keyframes pin{from{opacity:0;transform:scale(.96)translateY(6px)}to{opacity:1;transform:scale(1)translateY(0)}}`}</style>
      <div style={S.modal} onClick={e => e.stopPropagation()}>
        <div style={S.head}>
          <div style={S.title}>{agentOnly ? 'Select agent' : 'Add next node'}</div>
          <div style={S.sub}>
            {agentOnly
              ? 'Choose an A2A agent from the registry'
              : 'Choose from A2A agents, MCP servers, or logic steps'}
          </div>
        </div>

        <div style={S.sWrap}>
          <svg style={S.sIcon} fill='none' stroke='currentColor' viewBox='0 0 24 24'>
            <circle cx='11' cy='11' r='8' strokeWidth='2' />
            <path strokeLinecap='round' strokeLinejoin='round' strokeWidth='2' d='M21 21l-4.35-4.35' />
          </svg>
          <input
            style={S.sInput}
            placeholder='Search nodes…'
            value={query}
            onChange={e => setQuery(e.target.value)}
            autoFocus
          />
        </div>

        <div style={S.tabRow}>
          {activeTabs.map(t => (
            <button key={t} style={S.tab(tab === t)} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </div>

        <div style={S.list}>
          {/* ── Loading skeleton ── */}
          {isLoading && (
            <div style={{ textAlign: 'center', padding: '28px 0', fontSize: 12, color: 'var(--wf-text-4)' }}>
              Loading…
            </div>
          )}

          {/* ── A2A Agents ── */}
          {!isLoading && agentList.length > 0 && (
            <>
              <div style={S.sec}>A2A Agents</div>
              {agentList.map(a => (
                <div
                  key={a.id}
                  style={S.item(hovered === a.id, null)}
                  onMouseEnter={() => setHovered(a.id)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => onPick('agent', a)}
                >
                  <div style={S.icon('var(--wf-purple-tint)', 'var(--wf-purple-3)')}>{getInitials(a.label)}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={S.name()}>{a.label}</div>
                    <div style={S.key}>{a.desc}</div>
                  </div>
                  <div style={S.stat}>
                    <div style={{ ...S.dot, background: STATUS_COLOR[a.status] || 'var(--wf-text-4)' }} />
                    {STATUS_LABEL[a.status] || 'Unknown'}
                  </div>
                </div>
              ))}
            </>
          )}

          {/* ── MCP Servers ── */}
          {!isLoading && mcpList.length > 0 && (
            <>
              <div style={S.sec}>MCP Servers</div>
              {mcpList.map(m => (
                <div
                  key={m.id}
                  style={S.item(hovered === m.id, null)}
                  onMouseEnter={() => setHovered(m.id)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => onPick('mcp', m)}
                >
                  <div style={S.icon('var(--wf-btint)', 'var(--wf-blue)')}>{getInitials(m.label)}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={S.name()}>{m.label}</div>
                    <div style={S.key}>{m.desc}</div>
                  </div>
                  <div style={S.stat}>
                    <div style={{ ...S.dot, background: STATUS_COLOR[m.status] || 'var(--wf-text-4)' }} />
                    {STATUS_LABEL[m.status] || 'Unknown'}
                  </div>
                </div>
              ))}
            </>
          )}

          {/* ── Logic Steps ── */}
          {!isLoading && logicList.length > 0 && (
            <>
              <div style={S.sec}>Logic Step</div>
              {logicList.map(l => (
                <div
                  key={l.id}
                  style={S.item(hovered === l.id, l.color)}
                  onMouseEnter={() => setHovered(l.id)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => onPick('logic', l)}
                >
                  <div style={S.icon(l.accent, l.color, l.iconStyle || {})}>{l.icon}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={S.name(l.color)}>{l.label}</div>
                    <div style={S.key}>{l.desc}</div>
                  </div>
                </div>
              ))}
            </>
          )}

          {/* ── Empty state ── */}
          {!isLoading && noResults && (
            <div style={{ textAlign: 'center', padding: '28px 0', fontSize: 12, color: 'var(--wf-text-4)' }}>
              {query ? `No results for "${query}"` : 'No items found in registry'}
            </div>
          )}
        </div>

        <div style={S.foot}>
          <button style={S.cancel} onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
