import type { Node } from '@xyflow/react';
import type React from 'react';
import { useState } from 'react';
import { SelectField } from '@/components/FormFields/SelectField';
import type { LogicPropsProps, NodeData } from '../types';
import { CELContextReference } from './CELContextReference';

import { AddButton, BranchList } from './shared';

/** LogicProps - handles Gate, Cond, Router, and Loop node configuration. */
const LogicProps: React.FC<LogicPropsProps> = ({ node, nodes, edges, upstreamSchema, sourceLabel, onNodeDataChange }) => {
  const t = node.type;
  const nodeData = node.data as NodeData | undefined;

  const routerCases = Array.isArray(nodeData?.cases) ? (nodeData?.cases as string[]) : ['critical', 'normal'];
  const [loopAgent, setLoopAgent] = useState<{ id: string; label: string; desc: string } | null>(null);

  const onDataChange = (patch: Partial<NodeData>) => {
    onNodeDataChange(node.id, patch);
  };

  // Gate: HITL
  if (t === 'gate') {
    return (
      <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
        <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
          Human-in-the-loop
        </div>
        <div className='bg-[rgba(245,158,11,.06)] border border-[rgba(245,158,11,.22)] rounded-md p-3'>
          <div className='font-mono text-[10px] font-bold tracking-wide text-[var(--jarvis-warning)] mb-2'>
            ⏸ APPROVAL GATE
          </div>
          <div className='mb-2'>
            <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Reviewer prompt</label>
            <textarea
              className='resize-none h-13 leading-snug w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-sans text-xs px-2 py-1.5 outline-none'
              defaultValue='Review and approve to proceed, or reject to cancel.'
            />
          </div>
          <div className='mb-2'>
            <SelectField
              label='Approver role'
              options={[
                { value: 'engineer', label: 'Engineer' },
                { value: 'tech-lead', label: 'Tech Lead' },
                { value: 'any', label: 'Any member' },
              ]}
              value='engineer'
              onChange={() => {}}
            />
          </div>
          <div className='mb-2'>
            <SelectField
              label='Timeout'
              options={[
                { value: '24h', label: '24 hours' },
                { value: '4h', label: '4 hours' },
                { value: 'none', label: 'No timeout' },
              ]}
              value='24h'
              onChange={() => {}}
            />
          </div>
          <div className='mb-2'>
            <SelectField
              label='On timeout'
              options={[
                { value: 'cancel', label: 'Auto-cancel' },
                { value: 'escalate', label: 'Escalate' },
                { value: 'approve', label: 'Auto-approve' },
              ]}
              value='cancel'
              onChange={() => {}}
            />
          </div>
        </div>
      </div>
    );
  }

  // Cond: if / else
  if (t === 'cond') {
    const outEdges = edges.filter(e => e.source === node.id);
    const trueEdge = outEdges.find(e => e.sourceHandle === 'true');
    const falseEdge = outEdges.find(e => e.sourceHandle === 'false');
    const trueNode = trueEdge ? nodes.find(n => n.id === trueEdge.target) : null;
    const falseNode = falseEdge ? nodes.find(n => n.id === falseEdge.target) : null;
    const isAdd = (n: Node | null | undefined): boolean => n?.type === 'add';

    interface BranchSlotProps {
      label: string;
      color: string;
      targetNode: Node | null | undefined;
      icon: string;
    }

    const BranchSlot = ({ label, color, targetNode, icon }: BranchSlotProps) => (
      <div className='flex flex-col gap-1'>
        <div className='flex items-center gap-1.5'>
          <span className='text-[9px] font-mono font-bold tracking-wide uppercase' style={{ color }}>
            {label}
          </span>
          <span className='text-[9px]' style={{ color }}>
            {icon}
          </span>
        </div>
        {targetNode && !isAdd(targetNode) ? (
          <div
            className='flex items-center gap-1.5 bg-[var(--jarvis-card-muted)] rounded-md px-2 py-1.5'
            style={{ border: `1px solid ${color}33` }}
          >
            <div
              className='w-[22px] h-[22px] rounded flex items-center justify-center font-mono font-bold text-[8px] shrink-0'
              style={{ background: `${color}22`, color }}
            >
              {((targetNode.data?.label as string) || '')
                .split(' ')
                .map((w: string) => w[0])
                .join('')
                .slice(0, 2)
                .toUpperCase()}
            </div>
            <div className='flex-1 min-w-0'>
              <div className='text-[11.5px] font-medium text-[var(--jarvis-text-strong)] truncate'>
                {targetNode.data?.label as string}
              </div>
              <div className='text-[9px] text-[var(--jarvis-subtle)] mt-0.5'>{targetNode.type ?? 'unknown'} step</div>
            </div>
          </div>
        ) : (
          <div className='text-[11px] text-[var(--jarvis-subtle)] italic bg-[var(--jarvis-card-muted)] border border-dashed border-[var(--jarvis-border)] rounded-md px-2 py-1.5'>
            Not connected — draw an edge from the canvas
          </div>
        )}
      </div>
    );

    return (
      <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
        <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
          If / Else
        </div>
        <CELContextReference upstreamSchema={upstreamSchema} sourceLabel={sourceLabel} />
        <div className='mb-2'>
          <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>If — condition (CEL)</label>
          <input
            className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-mono text-[11px] px-2 py-1.5 outline-none'
            value={(nodeData?.expression as string | undefined) ?? 'session_state.score > 0.8'}
            onChange={e => onDataChange({ expression: e.target.value })}
          />
        </div>
        <div className='grid grid-cols-2 gap-2 mt-1'>
          <BranchSlot label='If true' color='var(--jarvis-blue)' targetNode={trueNode} icon='→' />
          <BranchSlot label='If false' color='var(--jarvis-subtle)' targetNode={falseNode} icon='↓' />
        </div>
        <p className='text-[11px] text-[var(--jarvis-subtle)] mt-2 leading-relaxed'>
          Connect nodes from the canvas — upper right handle for true, lower right handle for false.
        </p>
      </div>
    );
  }

  // Router: switch / case
  if (t === 'router') {
    return (
      <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
        <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
          Switch / case
        </div>
        <CELContextReference upstreamSchema={upstreamSchema} sourceLabel={sourceLabel} />
        <div className='mb-2'>
          <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Route by (CEL)</label>
          <input
            className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-mono text-[11px] px-2 py-1.5 outline-none'
            value={(nodeData?.routeBy as string | undefined) ?? 'session_state.severity'}
            onChange={e => onDataChange({ routeBy: e.target.value })}
          />
        </div>
        <div className='mb-1.5'>
          <div className='text-xs text-[var(--jarvis-muted)] mb-1'>Cases</div>
          <BranchList
            items={routerCases}
            onAdd={() => onDataChange({ cases: [...routerCases, ''] })}
            onRm={i => routerCases.length > 1 && onDataChange({ cases: routerCases.filter((_, j) => j !== i) })}
            onChange={(i, val) => {
              const next = [...routerCases];
              next[i] = val;
              onDataChange({ cases: next });
            }}
            addLabel='+ Add case'
            prefix='case'
          />
        </div>
        <div className='mb-2'>
          <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Default (fallthrough)</label>
          <input
            className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-sans text-[11.5px] px-2 py-1.5 outline-none'
            value={(nodeData?.defaultCase as string | undefined) ?? 'low-priority'}
            onChange={e => onDataChange({ defaultCase: e.target.value })}
          />
        </div>
      </div>
    );
  }

  // Loop: repeat with exit condition
  if (t === 'loop') {
    return (
      <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
        <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
          Loop config
        </div>
        <CELContextReference upstreamSchema={upstreamSchema} sourceLabel={sourceLabel} />
        <div className='mb-2'>
          <div className='text-xs text-[var(--jarvis-muted)] mb-1'>Agent (runs each iteration)</div>
          {loopAgent ? (
            <div className='flex items-center gap-2 bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md px-2 py-1.5'>
              <div className='w-6 h-6 rounded flex items-center justify-center font-mono font-bold text-[9px] shrink-0 bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'>
                {loopAgent.id
                  .split('-')
                  .map(w => w[0].toUpperCase())
                  .join('')
                  .slice(0, 2)}
              </div>
              <div className='flex-1 min-w-0'>
                <div className='text-xs font-medium text-[var(--jarvis-text-strong)]'>{loopAgent.label}</div>
                <div className='text-[10px] text-[var(--jarvis-subtle)]'>{loopAgent.desc}</div>
              </div>
              <button
                className='shrink-0 rounded p-0.5 transition-colors hover:bg-[var(--jarvis-danger-soft)] hover:text-[var(--jarvis-danger-text)] bg-none border-none text-[var(--jarvis-subtle)] cursor-pointer text-[13px]'
                onClick={() => setLoopAgent(null)}
              >
                ×
              </button>
            </div>
          ) : (
            <AddButton
              onClick={() => {
                /* TODO: implement agent picker callback */
              }}
            >
              + Select agent from registry
            </AddButton>
          )}
        </div>
        <div className='mb-2'>
          <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Max iterations</label>
          <input
            type='number'
            className='[appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none w-20 bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-sans text-xs px-2 py-1.5 outline-none'
            value={(nodeData?.maxIterations as number | undefined) ?? 5}
            onChange={e => onDataChange({ maxIterations: parseInt(e.target.value, 10) || 1 })}
            min={1}
          />
        </div>
        <div className='mb-2'>
          <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Continue while (CEL)</label>
          <input
            className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-mono text-[11px] px-2 py-1.5 outline-none'
            value='session_state.retry == true'
            onChange={() => {}}
          />
        </div>
        <div className='mb-2'>
          <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Exit when (CEL)</label>
          <input
            className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-mono text-[11px] px-2 py-1.5 outline-none'
            value={(nodeData?.exitCondition as string | undefined) ?? 'session_state.done == true'}
            onChange={e => onDataChange({ exitCondition: e.target.value })}
          />
        </div>
        <p className='text-[11px] text-[var(--jarvis-subtle)] leading-relaxed'>
          The selected agent runs on each iteration until the exit condition or max iterations is reached.
        </p>
      </div>
    );
  }

  return null;
};

export { LogicProps };
export default LogicProps;
