// @ts-nocheck
import React, { useCallback, useRef, useState } from 'react';

/* ── Run history mock data ── */
const RUNS = [
  { id: 'run_b04c71', status: 'live', time: 'Now · in progress', actions: ['pause', 'cancel'] },
  { id: 'run_a8f3bc', status: 'ok', time: 'Today 14:23 · attempt 1', dur: '0m 48s' },
  { id: 'run_72d1ef', status: 'ok', time: 'Today 09:11 · attempt 1', dur: '1m 03s' },
  { id: 'run_5c9a33', status: 'fail', time: 'Yesterday 18:44', err: 'timed out', actions: ['retry'] },
  { id: 'run_3d9f22', status: 'paused', time: 'Yesterday 11:02 · paused', actions: ['resume', 'cancel'] },
];

const RUN_COLORS = {
  ok: 'var(--wf-green)',
  fail: 'var(--wf-red)',
  live: 'var(--wf-amber)',
  paused: 'var(--wf-text-4)',
};
const RUN_GLOWS = { ok: 'var(--wf-gtint)', fail: 'var(--wf-rtint)', live: 'var(--wf-atint)' };
const ACTION_S = {
  pause: { color: 'var(--wf-amber)', borderColor: 'rgba(245,158,11,.3)', hoverBg: 'var(--wf-atint)' },
  cancel: { color: 'var(--wf-red)', borderColor: 'rgba(239,68,68,.25)', hoverBg: 'var(--wf-rtint)' },
  resume: { color: 'var(--wf-green)', borderColor: 'rgba(16,185,129,.3)', hoverBg: 'var(--wf-gtint)' },
  retry: { color: '#38bdf8', borderColor: 'var(--wf-btint)', hoverBg: 'var(--wf-btint)' },
};
const ACTION_LABELS = { pause: '⏸', cancel: '✕', resume: '▶', retry: '↻' };

/* CEL_CONTEXT removed — variables are now derived from the upstream node’s
   output schema (simulating GET /agents/{id}/schema from the Agno backend). */

function RunRow({ run }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 8,
        background: 'var(--wf-bg-2)',
        border: '1px solid var(--wf-border)',
        borderRadius: 9,
        padding: '9px 10px',
        marginBottom: 6,
      }}
    >
      <div
        style={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          flexShrink: 0,
          marginTop: 3,
          background: RUN_COLORS[run.status],
          boxShadow: RUN_GLOWS[run.status] ? `0 0 0 2px ${RUN_GLOWS[run.status]}` : 'none',
          animation: run.status === 'live' ? 'pulse 1.2s infinite' : 'none',
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontFamily: 'JetBrains Mono,monospace',
            fontSize: 11,
            fontWeight: 500,
            color: 'var(--wf-text-2)',
            marginBottom: 2,
          }}
        >
          {run.id}
        </div>
        <div style={{ fontSize: 11, color: 'var(--wf-text-4)' }}>{run.time}</div>
        {run.err && (
          <span
            style={{
              display: 'inline-block',
              marginTop: 4,
              fontSize: 9,
              fontFamily: 'JetBrains Mono,monospace',
              padding: '2px 5px',
              borderRadius: 3,
              background: 'var(--wf-rtint)',
              border: '1px solid rgba(239,68,68,.25)',
              color: 'var(--wf-red)',
            }}
          >
            {run.err}
          </span>
        )}
      </div>
      {run.dur && (
        <div style={{ fontFamily: 'JetBrains Mono,monospace', fontSize: 11, color: 'var(--wf-text-4)', flexShrink: 0 }}>
          {run.dur}
        </div>
      )}
      {run.actions && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3, flexShrink: 0 }}>
          {run.actions.map(a => {
            const as = ACTION_S[a];
            return (
              <button
                key={a}
                style={{
                  background: 'none',
                  border: `1px solid ${as.borderColor}`,
                  borderRadius: 5,
                  color: as.color,
                  cursor: 'pointer',
                  padding: '3px 7px',
                  fontFamily: 'Inter,sans-serif',
                  fontSize: 10,
                  fontWeight: 500,
                }}
              >
                {ACTION_LABELS[a]}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ── Properties content per node type ── */
const F = {
  sec: { padding: '13px 15px', borderBottom: '1px solid var(--wf-border)' },
  sl: {
    fontFamily: 'JetBrains Mono,monospace',
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '.08em',
    textTransform: 'uppercase',
    color: 'var(--wf-text-4)',
    marginBottom: 9,
  },
  field: { marginBottom: 9 },
  label: { display: 'block', fontSize: 12, color: 'var(--wf-text-3)', marginBottom: 3 },
  inp: {
    width: '100%',
    background: 'var(--wf-bg-2)',
    border: '1px solid var(--wf-border)',
    borderRadius: 7,
    color: 'var(--wf-text-1)',
    fontFamily: 'Inter,sans-serif',
    fontSize: 12,
    padding: '7px 9px',
    outline: 'none',
  },
  ta: {
    resize: 'none',
    height: 52,
    lineHeight: 1.4,
    width: '100%',
    background: 'var(--wf-bg-2)',
    border: '1px solid var(--wf-border)',
    borderRadius: 7,
    color: 'var(--wf-text-1)',
    fontFamily: 'Inter,sans-serif',
    fontSize: 12,
    padding: '7px 9px',
    outline: 'none',
  },
  sel: {
    width: '100%',
    background: 'var(--wf-bg-2)',
    border: '1px solid var(--wf-border)',
    borderRadius: 7,
    color: 'var(--wf-text-1)',
    fontFamily: 'Inter,sans-serif',
    fontSize: 12,
    padding: '7px 9px',
    outline: 'none',
  },
  hitl: { background: 'rgba(245,158,11,.06)', border: '1px solid rgba(245,158,11,.22)', borderRadius: 8, padding: 11 },
  hlbl: {
    fontFamily: 'JetBrains Mono,monospace',
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '.08em',
    color: 'var(--wf-amber)',
    marginBottom: 9,
  },
  bitem: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    background: 'var(--wf-bg-2)',
    border: '1px solid var(--wf-border)',
    borderRadius: 6,
    padding: '5px 8px',
    marginBottom: 4,
  },
  binp: {
    fontFamily: 'JetBrains Mono,monospace',
    fontSize: 11,
    color: 'var(--wf-text-2)',
    flex: 1,
    background: 'transparent',
    border: 'none',
    outline: 'none',
  },
  brm: {
    background: 'none',
    border: 'none',
    color: 'var(--wf-text-4)',
    cursor: 'pointer',
    fontSize: 13,
    padding: '0 2px',
    flexShrink: 0,
  },
  badd: {
    width: '100%',
    background: 'none',
    border: '1px dashed var(--wf-border-strong)',
    borderRadius: 6,
    color: 'var(--wf-text-4)',
    fontFamily: 'Inter,sans-serif',
    fontSize: 12,
    padding: 6,
    cursor: 'pointer',
  },
  mono: {
    width: '100%',
    background: 'var(--wf-bg-2)',
    border: '1px solid var(--wf-border)',
    borderRadius: 7,
    color: 'var(--wf-text-1)',
    fontFamily: 'JetBrains Mono,monospace',
    fontSize: 11,
    padding: '7px 9px',
    outline: 'none',
  },
  hint: { fontSize: 11, color: 'var(--wf-text-4)', marginTop: 6, lineHeight: 1.45 },
};

function useBranchState(initial) {
  const [items, setItems] = useState(initial);
  const add = val => setItems(prev => [...prev, val]);
  const rm = i => setItems(prev => prev.filter((_, j) => j !== i));
  return [items, add, rm];
}

function BranchList({ items, onAdd, onRm, addLabel, prefix }) {
  return (
    <>
      <div className='branch-list'>
        {items.map((item, i) => (
          <div key={i} style={F.bitem}>
            {prefix && (
              <span
                style={{
                  fontFamily: 'JetBrains Mono,monospace',
                  fontSize: 10,
                  color: 'var(--wf-text-4)',
                  flexShrink: 0,
                }}
              >
                {prefix}
              </span>
            )}
            <input style={F.binp} defaultValue={item} />
            <button style={F.brm} onClick={() => onRm(i)}>
              ×
            </button>
          </div>
        ))}
      </div>
      <button style={F.badd} onClick={onAdd}>
        {addLabel}
      </button>
    </>
  );
}

/* ── CEL Context Reference Component ──
   Receives `upstreamSchema` derived from GET /agents/{id}/schema output[]
   and the source node label so engineers know where variables come from. */
function CELContextReference({ upstreamSchema, sourceLabel }) {
  if (!upstreamSchema || upstreamSchema.length === 0) {
    return (
      <div
        style={{
          background: 'rgba(99,102,241,0.06)',
          border: '1px solid rgba(99,102,241,0.15)',
          borderRadius: 7,
          padding: 10,
          marginBottom: 12,
        }}
      >
        <div
          style={{
            fontFamily: 'JetBrains Mono,monospace',
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: '.05em',
            color: 'rgba(99,102,241,0.8)',
            textTransform: 'uppercase',
            marginBottom: 4,
          }}
        >
          Available Variables
        </div>
        <div style={{ fontSize: 10, color: 'var(--wf-text-4)', fontStyle: 'italic' }}>
          Connect a node to see its output variables here.
        </div>
      </div>
    );
  }
  return (
    <div
      style={{
        background: 'rgba(99,102,241,0.06)',
        border: '1px solid rgba(99,102,241,0.15)',
        borderRadius: 7,
        padding: 10,
        marginBottom: 12,
      }}
    >
      {/* Header: explains WHERE these variables come from */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div
          style={{
            fontFamily: 'JetBrains Mono,monospace',
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: '.05em',
            color: 'rgba(99,102,241,0.8)',
            textTransform: 'uppercase',
          }}
        >
          Available Variables
        </div>
        {sourceLabel && (
          <div style={{ fontSize: 9, color: 'var(--wf-text-4)', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ opacity: 0.5 }}>from</span>
            <span style={{ fontFamily: 'JetBrains Mono,monospace', color: 'var(--wf-blue)', fontWeight: 600 }}>
              {sourceLabel}
            </span>
            <span
              style={{ fontFamily: 'JetBrains Mono,monospace', fontSize: 8, color: 'var(--wf-text-4)', opacity: 0.6 }}
            >
              /schema
            </span>
          </div>
        )}
      </div>
      <div style={{ display: 'grid', gap: 7 }}>
        {upstreamSchema.map((v, i) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 8, alignItems: 'start' }}>
            <code
              style={{
                fontFamily: 'JetBrains Mono,monospace',
                fontSize: 10,
                color: 'var(--wf-blue)',
                fontWeight: 600,
                whiteSpace: 'nowrap',
              }}
            >
              {v.name}
            </code>
            <div style={{ fontSize: 10, color: 'var(--wf-text-4)', lineHeight: 1.3 }}>
              <div style={{ color: 'var(--wf-text-3)' }}>{v.desc}</div>
              <div style={{ display: 'flex', gap: 8, marginTop: 2, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 9, color: 'var(--wf-text-4)' }}>type: {v.type}</span>
                {v.enum && <span style={{ fontSize: 9, color: 'var(--wf-amber)' }}>{v.enum.join(' | ')}</span>}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function NodeProps({ node, upstreamSchema, sourceLabel, onOpenAgentPicker, onNodeDataChange, onParallelBranchesChange, edges = [], nodes = [] }) {
  /* ── Parallel branches ──
     Initialise from node.data.branches; keep in sync when node changes;        */
  const initBranches = Array.isArray(node?.data?.branches) ? node.data.branches : ['Branch A', 'Branch B'];
  const [parBranches, setParBranches] = useState(initBranches);

  /* Sync when a different Parallel node is selected */
  const prevNodeId = React.useRef(node?.id);
  React.useEffect(() => {
    if (node?.id !== prevNodeId.current) {
      prevNodeId.current = node?.id;
      if (node?.type === 'parallel') {
        setParBranches(Array.isArray(node.data?.branches) ? node.data.branches : ['Branch A', 'Branch B']);
      }
    }
  }, [node?.id, node?.type, node?.data?.branches]);

  const addPar = (val) => {
    const next = [...parBranches, val];
    setParBranches(next);
    onParallelBranchesChange?.(node.id, parBranches, next);
  };
  const rmPar = (i) => {
    const next = parBranches.filter((_, j) => j !== i);
    setParBranches(next);
    onParallelBranchesChange?.(node.id, parBranches, next);
  };

  const [routerCases, addCase, rmCase] = useBranchState(['critical', 'normal']);
  /* Pool: store agent objects { id, label, desc } */
  const [poolAgents, setPoolAgents] = useState([
    { id: 'classifier', label: 'Classifier Agent', desc: 'NLP categorization' },
    { id: 'remediation', label: 'Remediation Agent', desc: 'Executes automated fixes' },
  ]);
  /* Loop: single agent */
  const [loopAgent, setLoopAgent] = useState(null);

  if (!node) {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          gap: 10,
          padding: 28,
        }}
      >
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 10,
            background: 'var(--wf-bg-2)',
            border: '1px solid var(--wf-border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <svg fill='none' stroke='currentColor' viewBox='0 0 24 24' width='17' height='17'>
            <path strokeLinecap='round' strokeLinejoin='round' strokeWidth='1.5' d='M4 5h16M4 12h10M4 19h6' />
          </svg>
        </div>
        <p style={{ fontSize: 12, color: 'var(--wf-text-4)', textAlign: 'center', lineHeight: 1.55 }}>
          Click any node to view its properties and run history
        </p>
      </div>
    );
  }

  const t = node.type;

  return (
    <>
      {/* Common: title */}
      <div style={F.sec}>
        <div style={F.sl}>Node</div>
        <div style={F.field}>
          <label style={F.label}>Title</label>
          <input style={F.inp} defaultValue={node.data?.label || ''} key={node.id} />
        </div>
      </div>

      {/* Gate: HITL */}
      {t === 'gate' && (
        <div style={F.sec}>
          <div style={F.sl}>Human-in-the-loop</div>
          <div style={F.hitl}>
            <div style={F.hlbl}>⏸ APPROVAL GATE</div>
            <div style={F.field}>
              <label style={F.label}>Reviewer prompt</label>
              <textarea style={F.ta} defaultValue='Review and approve to proceed, or reject to cancel.' />
            </div>
            <div style={F.field}>
              <label style={F.label}>Approver role</label>
              <select style={F.sel}>
                <option>Engineer</option>
                <option>Tech Lead</option>
                <option>Any member</option>
              </select>
            </div>
            <div style={F.field}>
              <label style={F.label}>Timeout</label>
              <select style={F.sel}>
                <option>24 hours</option>
                <option>4 hours</option>
                <option>No timeout</option>
              </select>
            </div>
            <div style={F.field}>
              <label style={F.label}>On timeout</label>
              <select style={F.sel}>
                <option>Auto-cancel</option>
                <option>Escalate</option>
                <option>Auto-approve</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {/* Cond: if / else */}
      {t === 'cond' &&
        (() => {
          /* Walk outgoing edges to find what each branch connects to */
          const outEdges = edges.filter(e => e.source === node.id);
          const trueEdge = outEdges.find(e => e.sourceHandle === 'true');
          const falseEdge = outEdges.find(e => e.sourceHandle === 'false');
          const trueNode = trueEdge ? nodes.find(n => n.id === trueEdge.target) : null;
          const falseNode = falseEdge ? nodes.find(n => n.id === falseEdge.target) : null;
          const isAdd = n => n?.type === 'add';

          const BranchSlot = ({ label, color, targetNode, icon }) => (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span
                  style={{
                    fontSize: 9,
                    fontFamily: 'JetBrains Mono,monospace',
                    fontWeight: 700,
                    letterSpacing: '.04em',
                    color,
                    textTransform: 'uppercase',
                  }}
                >
                  {label}
                </span>
                <span style={{ fontSize: 9, color }}>{icon}</span>
              </div>
              {targetNode && !isAdd(targetNode) ? (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 7,
                    background: 'var(--wf-bg-2)',
                    border: `1px solid ${color}33`,
                    borderRadius: 7,
                    padding: '6px 8px',
                  }}
                >
                  <div
                    style={{
                      width: 22,
                      height: 22,
                      borderRadius: 5,
                      background: color + '22',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontFamily: 'JetBrains Mono,monospace',
                      fontWeight: 700,
                      fontSize: 8,
                      color,
                      flexShrink: 0,
                    }}
                  >
                    {targetNode.data?.label
                      ?.split(' ')
                      .map(w => w[0])
                      .join('')
                      .slice(0, 2)
                      .toUpperCase()}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 11.5,
                        fontWeight: 500,
                        color: 'var(--wf-text-1)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {targetNode.data?.label}
                    </div>
                    <div style={{ fontSize: 9, color: 'var(--wf-text-4)', marginTop: 1 }}>{targetNode.type} step</div>
                  </div>
                </div>
              ) : (
                <div
                  style={{
                    fontSize: 11,
                    color: 'var(--wf-text-4)',
                    fontStyle: 'italic',
                    background: 'var(--wf-bg-2)',
                    border: '1px dashed var(--wf-border)',
                    borderRadius: 7,
                    padding: '7px 9px',
                  }}
                >
                  Not connected — draw an edge from the canvas
                </div>
              )}
            </div>
          );

          return (
            <div style={F.sec}>
              <div style={F.sl}>If / Else</div>
              <CELContextReference upstreamSchema={upstreamSchema} sourceLabel={sourceLabel} />
              <div style={F.field}>
                <label style={F.label}>If — condition (CEL)</label>
                <input style={F.mono} defaultValue={node.data?.expression || 'session_state.score > 0.8'} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 4 }}>
                <BranchSlot label='If true' color='var(--wf-blue)' targetNode={trueNode} icon='→' />
                <BranchSlot label='If false' color='var(--wf-text-4)' targetNode={falseNode} icon='↓' />
              </div>
              <p style={{ ...F.hint, marginTop: 8 }}>
                Connect nodes from the canvas — upper right handle for true, lower right handle for false.
              </p>
            </div>
          );
        })()}

      {/* Parallel: unlimited branches */}
      {t === 'parallel' && (
        <div style={F.sec}>
          <div style={F.sl}>
            Branches <span style={{ color: 'var(--wf-text-4)', fontWeight: 400 }}>(no limit)</span>
          </div>
          <BranchList
            items={parBranches}
            onAdd={() => {
              const L = 'ABCDEFGHIJKLMNOP';
              addPar('Branch ' + L[parBranches.length]);
            }}
            onRm={i => parBranches.length > 1 && rmPar(i)}
            addLabel='+ Add branch'
          />
          <p style={F.hint}>
            Each branch fans out independently. Add the next node after each branch output on the canvas.
          </p>
        </div>
      )}

      {/* Router: switch / case */}
      {t === 'router' && (
        <div style={F.sec}>
          <div style={F.sl}>Switch / case</div>
          <CELContextReference upstreamSchema={upstreamSchema} sourceLabel={sourceLabel} />
          <div style={F.field}>
            <label style={F.label}>Route by (CEL)</label>
            <input style={F.mono} defaultValue={node.data?.routeBy || 'session_state.severity'} />
          </div>
          <div style={{ marginBottom: 6 }}>
            <div style={{ fontSize: 12, color: 'var(--wf-text-3)', marginBottom: 3 }}>Cases</div>
            <BranchList
              items={routerCases}
              onAdd={() => addCase('')}
              onRm={i => routerCases.length > 1 && rmCase(i)}
              addLabel='+ Add case'
              prefix='case'
            />
          </div>
          <div style={F.field}>
            <label style={F.label}>Default (fallthrough)</label>
            <input style={{ ...F.inp, fontSize: 11.5 }} defaultValue='low-priority' />
          </div>
        </div>
      )}

      {/* Loop: repeat with exit condition */}
      {t === 'loop' && (
        <div style={F.sec}>
          <div style={F.sl}>Loop config</div>
          <CELContextReference upstreamSchema={upstreamSchema} sourceLabel={sourceLabel} />
          {/* Agent that runs each iteration */}
          <div style={{ marginBottom: 9 }}>
            <div style={{ fontSize: 12, color: 'var(--wf-text-3)', marginBottom: 4 }}>Agent (runs each iteration)</div>
            {loopAgent ? (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  background: 'var(--wf-bg-2)',
                  border: '1px solid var(--wf-border)',
                  borderRadius: 7,
                  padding: '7px 9px',
                }}
              >
                <div
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: 6,
                    background: 'var(--wf-purple-tint)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontFamily: 'JetBrains Mono,monospace',
                    fontWeight: 700,
                    fontSize: 9,
                    color: 'var(--wf-purple-3)',
                    flexShrink: 0,
                  }}
                >
                  {loopAgent.id
                    .split('-')
                    .map(w => w[0].toUpperCase())
                    .join('')
                    .slice(0, 2)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--wf-text-1)' }}>{loopAgent.label}</div>
                  <div style={{ fontSize: 10, color: 'var(--wf-text-4)' }}>{loopAgent.desc}</div>
                </div>
                <button style={F.brm} onClick={() => setLoopAgent(null)}>
                  ×
                </button>
              </div>
            ) : (
              <button style={F.badd} onClick={() => onOpenAgentPicker(agent => setLoopAgent(agent))}>
                + Select agent from registry
              </button>
            )}
          </div>
          <div style={F.field}>
            <label style={F.label}>Max iterations</label>
            <input style={{ ...F.inp, width: 80 }} type='number' defaultValue={node.data?.maxIterations || 5} />
          </div>
          <div style={F.field}>
            <label style={F.label}>Continue while (CEL)</label>
            <input style={F.mono} defaultValue='session_state.retry == true' />
          </div>
          <div style={F.field}>
            <label style={F.label}>Exit when (CEL)</label>
            <input style={F.mono} defaultValue={node.data?.exitCondition || 'session_state.done == true'} />
          </div>
          <p style={F.hint}>
            The selected agent runs on each iteration until the exit condition or max iterations is reached.
          </p>
        </div>
      )}

      {/* Pool: up to 5 delegate agents */}
      {t === 'pool' && (
        <div style={F.sec}>
          <div style={F.sl}>
            Delegate agents{' '}
            <span style={{ color: 'var(--wf-text-4)', fontWeight: 400 }}>({poolAgents.length} / 5)</span>
          </div>
          <div>
            {poolAgents.map((a, i) => (
              <div
                key={a.id + i}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  background: 'var(--wf-bg-2)',
                  border: '1px solid var(--wf-border)',
                  borderRadius: 7,
                  padding: '7px 9px',
                  marginBottom: 4,
                }}
              >
                <div
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: 6,
                    background: 'var(--wf-purple-tint)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontFamily: 'JetBrains Mono,monospace',
                    fontWeight: 700,
                    fontSize: 9,
                    color: 'var(--wf-purple-3)',
                    flexShrink: 0,
                  }}
                >
                  {a.id
                    .split('-')
                    .map(w => w[0].toUpperCase())
                    .join('')
                    .slice(0, 2)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--wf-text-1)' }}>{a.label}</div>
                  <div style={{ fontSize: 10, color: 'var(--wf-text-4)' }}>{a.desc}</div>
                </div>
                <button style={F.brm} onClick={() => setPoolAgents(prev => prev.filter((_, j) => j !== i))}>
                  ×
                </button>
              </div>
            ))}
            {poolAgents.length < 5 && (
              <button
                style={F.badd}
                onClick={() =>
                  onOpenAgentPicker(agent => {
                    setPoolAgents(prev => (prev.find(a => a.id === agent.id) ? prev : [...prev, agent]));
                  })
                }
              >
                + Add agent from registry
              </button>
            )}
          </div>
          <p style={F.hint}>The LLM selects the best-fit agent at runtime. All agents share a single output edge.</p>
        </div>
      )}
    </>
  );
}

/* ── Main PropsPanel — collapsible + draggable width ── */
const MIN_W = 200;
const MAX_W = 480;
const DEFAULT_W = 264;

export default function PropsPanel({ selectedNode, nodes = [], edges = [], agentSchemas = {}, onOpenAgentPicker, onNodeDataChange, onParallelBranchesChange, collapsed = false, onCollapsedChange }) {
  const [tab, setTab] = useState('props'); // 'props' | 'hist'
  const [width, setWidth] = useState(DEFAULT_W);
  const setCollapsed = (val) => onCollapsedChange?.(typeof val === 'function' ? val(collapsed) : val);
  const draggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWRef = useRef(DEFAULT_W);

  /* ── Derive upstream node schema whenever selectedNode changes ── */
  /* Walk edges backward: find the edge whose target === selectedNode.id */
  const CEL_STEPS = ['cond', 'router', 'loop'];
  const upstreamSchema = React.useMemo(() => {
    if (!selectedNode || !CEL_STEPS.includes(selectedNode.type)) return null;
    const incomingEdge = edges.find(e => e.target === selectedNode.id);
    if (!incomingEdge) return null;
    const sourceNode = nodes.find(n => n.id === incomingEdge.source);
    if (!sourceNode) return null;
    return agentSchemas[sourceNode.data?.label]?.output ?? null;
  }, [selectedNode, edges, nodes, agentSchemas]);

  const sourceLabel = React.useMemo(() => {
    if (!selectedNode || !CEL_STEPS.includes(selectedNode.type)) return null;
    const incomingEdge = edges.find(e => e.target === selectedNode.id);
    if (!incomingEdge) return null;
    const sourceNode = nodes.find(n => n.id === incomingEdge.source);
    return sourceNode?.data?.label ?? null;
  }, [selectedNode, edges, nodes]);

  /* ── Drag-to-resize handle ── */
  const onResizeStart = useCallback(
    e => {
      e.preventDefault();
      draggingRef.current = true;
      startXRef.current = e.clientX;
      startWRef.current = width;

      const onMove = mv => {
        if (!draggingRef.current) return;
        const delta = startXRef.current - mv.clientX; // dragging left = wider
        const newW = Math.min(MAX_W, Math.max(MIN_W, startWRef.current + delta));
        setWidth(newW);
      };
      const onUp = () => {
        draggingRef.current = false;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [width],
  );

  const panelW = collapsed ? 36 : width;

  return (
    <div style={{ display: 'flex', flexShrink: 0, position: 'relative', height: '100%' }}>
      {/* ── Resize handle ── */}
      {!collapsed && (
        <div
          onMouseDown={onResizeStart}
          style={{
            width: 4,
            cursor: 'col-resize',
            background: 'transparent',
            flexShrink: 0,
            transition: 'background .15s',
            zIndex: 10,
          }}
          onMouseEnter={e => (e.currentTarget.style.background = 'var(--wf-purple-1)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
        />
      )}

      {/* ── Panel ── */}
      <div
        style={{
          width: panelW,
          background: 'var(--wf-bg-1)',
          borderLeft: '1px solid var(--wf-border)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          transition: 'width var(--wf-t)',
          flexShrink: 0,
          height: '100%',
        }}
      >
        {/* ── Collapse toggle + tabs ── */}
        <div
          style={{ display: 'flex', alignItems: 'center', borderBottom: '1px solid var(--wf-border)', flexShrink: 0 }}
        >
          {/* Collapse/expand chevron */}
          <button
            onClick={() => setCollapsed(c => !c)}
            title={collapsed ? 'Expand panel' : 'Collapse panel'}
            style={{
              width: 36,
              height: 42,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'none',
              border: 'none',
              color: 'var(--wf-text-4)',
              cursor: 'pointer',
              flexShrink: 0,
              transition: 'color var(--wf-t)',
            }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--wf-text-1)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'var(--wf-text-4)')}
          >
            <svg width='14' height='14' fill='none' stroke='currentColor' viewBox='0 0 24 24'>
              <path
                strokeLinecap='round'
                strokeLinejoin='round'
                strokeWidth='2'
                d={collapsed ? 'M15 19l-7-7 7-7' : 'M9 5l7 7-7 7'}
              />
            </svg>
          </button>

          {!collapsed && (
            <>
              <button
                style={{
                  flex: 1,
                  padding: '11px 6px',
                  textAlign: 'center',
                  fontFamily: 'Inter,sans-serif',
                  fontSize: 11.5,
                  fontWeight: 500,
                  color: tab === 'props' ? 'var(--wf-purple-3)' : 'var(--wf-text-4)',
                  cursor: 'pointer',
                  background: 'none',
                  border: 'none',
                  borderBottom: tab === 'props' ? '2px solid var(--wf-purple-2)' : '2px solid transparent',
                  transition: 'all var(--wf-t)',
                }}
                onClick={() => setTab('props')}
              >
                Properties
              </button>
              <button
                style={{
                  flex: 1,
                  padding: '11px 6px',
                  textAlign: 'center',
                  fontFamily: 'Inter,sans-serif',
                  fontSize: 11.5,
                  fontWeight: 500,
                  color: tab === 'hist' ? 'var(--wf-purple-3)' : 'var(--wf-text-4)',
                  cursor: 'pointer',
                  background: 'none',
                  border: 'none',
                  borderBottom: tab === 'hist' ? '2px solid var(--wf-purple-2)' : '2px solid transparent',
                  transition: 'all var(--wf-t)',
                }}
                onClick={() => setTab('hist')}
              >
                Run history
              </button>
            </>
          )}
        </div>

        {/* ── Panel body ── */}
        {!collapsed && (
          <div style={{ flex: 1, overflowY: 'auto' }}>
            <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}`}</style>

            {tab === 'props' && (
              <NodeProps
                node={selectedNode}
                upstreamSchema={upstreamSchema}
                sourceLabel={sourceLabel}
                onOpenAgentPicker={onOpenAgentPicker}
                onNodeDataChange={onNodeDataChange}
                onParallelBranchesChange={onParallelBranchesChange}
                edges={edges}
                nodes={nodes}
              />
            )}

            {tab === 'hist' && (
              <div style={{ padding: '13px 15px' }}>
                <div
                  style={{
                    fontFamily: 'JetBrains Mono,monospace',
                    fontSize: 10,
                    fontWeight: 700,
                    letterSpacing: '.08em',
                    textTransform: 'uppercase',
                    color: 'var(--wf-text-4)',
                    marginBottom: 6,
                  }}
                >
                  {selectedNode ? `${selectedNode.data?.label} — run history` : 'Run history'}
                </div>
                {selectedNode ? (
                  RUNS.map(r => <RunRow key={r.id} run={r} />)
                ) : (
                  <p style={{ fontSize: 12, color: 'var(--wf-text-4)', textAlign: 'center', padding: '24px 0' }}>
                    Select a node to view its run history
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
