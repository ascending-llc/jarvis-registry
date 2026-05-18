import { TrashIcon } from '@heroicons/react/24/outline';
import type { Node } from '@xyflow/react';
import React, { useCallback, useRef, useState } from 'react';
import type { NodeData, PropsPanelProps, SchemaField } from '../types';
import { LogicProps } from './LogicProps';
import { ParallelProps } from './ParallelProps';
import { PoolProps } from './PoolProps';
import { RunHistory } from './RunHistory';

const MIN_W = 200;
const MAX_W = 480;
const DEFAULT_W = 264;

/** PropsPanel - collapsible + draggable width properties panel. */
const PropsPanel: React.FC<PropsPanelProps> = ({
  workflowId,
  selectedNode,
  nodes = [],
  edges = [],
  agentSchemas = {},
  onOpenAgentPicker,
  onNodeDataChange,
  onParallelBranchesChange,
  onDeleteNode,
  collapsed = false,
  onCollapsedChange,
}) => {
  const [tab, setTab] = useState<'props' | 'hist'>('props');
  const [width, setWidth] = useState(DEFAULT_W);
  const setCollapsed = (val: boolean | ((prev: boolean) => boolean)) =>
    onCollapsedChange?.(typeof val === 'function' ? val(collapsed) : val);
  const draggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWRef = useRef(DEFAULT_W);

  const CEL_STEPS = ['cond', 'router', 'loop'];
  const upstreamSchema: SchemaField[] | null = React.useMemo(() => {
    if (!selectedNode || !CEL_STEPS.includes(selectedNode.type ?? '')) return null;
    const incomingEdge = edges.find(e => e.target === selectedNode.id);
    if (!incomingEdge) return null;
    const sourceNode = nodes.find(n => n.id === incomingEdge.source);
    if (!sourceNode) return null;
    const label = sourceNode.data?.label as string | undefined;
    return label ? (agentSchemas[label]?.output ?? null) : null;
  }, [selectedNode, edges, nodes, agentSchemas]);

  const sourceLabel: string | null = React.useMemo(() => {
    if (!selectedNode || !CEL_STEPS.includes(selectedNode.type ?? '')) return null;
    const incomingEdge = edges.find(e => e.target === selectedNode.id);
    if (!incomingEdge) return null;
    const sourceNode = nodes.find(n => n.id === incomingEdge.source);
    return (sourceNode?.data?.label as string | undefined) ?? null;
  }, [selectedNode, edges, nodes]);

  const onResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      draggingRef.current = true;
      startXRef.current = e.clientX;
      startWRef.current = width;

      const onMove = (mv: MouseEvent) => {
        if (!draggingRef.current) return;
        const delta = startXRef.current - mv.clientX;
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

  const panelW = collapsed ? 0 : width;

  if (!selectedNode) {
    return (
      <div className='flex shrink-0 relative h-full'>
        {collapsed && (
          <button
            onClick={() => setCollapsed(false)}
            title='Expand panel'
            className='absolute right-0 top-0 w-9 h-[42px] z-50 flex items-center justify-center bg-[var(--jarvis-card)] border-b border-l border-[var(--jarvis-border)] text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text-strong)] rounded-bl-md cursor-pointer shadow-sm transition-colors duration-200'
          >
            <svg width='14' height='14' fill='none' stroke='currentColor' viewBox='0 0 24 24'>
              <path strokeLinecap='round' strokeLinejoin='round' strokeWidth='2' d='M15 19l-7-7 7-7' />
            </svg>
          </button>
        )}
        <div
          className='bg-[var(--jarvis-card)] border-l border-[var(--jarvis-border)] flex flex-col overflow-hidden shrink-0 h-full transition-all duration-200 ease-out'
          style={{ width: panelW }}
        >
          {!collapsed && (
            <div className='flex items-center border-b border-[var(--jarvis-border)] shrink-0'>
              <button
                onClick={() => setCollapsed(true)}
                title='Collapse panel'
                className='w-9 h-[42px] flex items-center justify-center bg-none border-none text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text-strong)] cursor-pointer shrink-0 transition-colors duration-200'
              >
                <svg width='14' height='14' fill='none' stroke='currentColor' viewBox='0 0 24 24'>
                  <path strokeLinecap='round' strokeLinejoin='round' strokeWidth='2' d='M9 5l7 7-7 7' />
                </svg>
              </button>
            </div>
          )}
          {!collapsed && (
            <div className='flex-1 overflow-y-auto'>
              <div className='flex flex-col items-center justify-center h-full gap-2.5 p-7'>
                <div className='w-10 h-10 rounded-lg bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] flex items-center justify-center'>
                  <svg fill='none' stroke='currentColor' viewBox='0 0 24 24' width='17' height='17'>
                    <path strokeLinecap='round' strokeLinejoin='round' strokeWidth='1.5' d='M4 5h16M4 12h10M4 19h6' />
                  </svg>
                </div>
                <p className='text-xs text-[var(--jarvis-subtle)] text-center leading-relaxed'>
                  Click any node to view its properties and run history
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  const t = selectedNode.type;
  const nodeData = selectedNode.data as NodeData | undefined;

  return (
    <div className='flex shrink-0 relative h-full'>
      {!collapsed && (
        <div
          onMouseDown={onResizeStart}
          className='w-1 cursor-col-resize shrink-0 z-10 transition-colors duration-150 hover:bg-[var(--jarvis-primary)]'
        />
      )}

      {collapsed && (
        <button
          onClick={() => setCollapsed(false)}
          title='Expand panel'
          className='absolute right-0 top-0 w-9 h-[42px] z-50 flex items-center justify-center bg-[var(--jarvis-card)] border-b border-l border-[var(--jarvis-border)] text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text-strong)] rounded-bl-md cursor-pointer shadow-sm transition-colors duration-200'
        >
          <svg width='14' height='14' fill='none' stroke='currentColor' viewBox='0 0 24 24'>
            <path strokeLinecap='round' strokeLinejoin='round' strokeWidth='2' d='M15 19l-7-7 7-7' />
          </svg>
        </button>
      )}

      <div
        className='bg-[var(--jarvis-card)] border-l border-[var(--jarvis-border)] flex flex-col overflow-hidden shrink-0 h-full transition-all duration-200 ease-out'
        style={{ width: panelW }}
      >
        {!collapsed && (
          <div className='flex items-center border-b border-[var(--jarvis-border)] shrink-0'>
            <button
              onClick={() => setCollapsed(true)}
              title='Collapse panel'
              className='w-9 h-[42px] flex items-center justify-center bg-none border-none text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text-strong)] cursor-pointer shrink-0 transition-colors duration-200'
            >
              <svg width='14' height='14' fill='none' stroke='currentColor' viewBox='0 0 24 24'>
                <path strokeLinecap='round' strokeLinejoin='round' strokeWidth='2' d='M9 5l7 7-7 7' />
              </svg>
            </button>

            <button
              className='flex-1 px-1.5 py-2.5 text-center font-sans text-[11.5px] font-medium cursor-pointer bg-none border-none transition-all duration-200'
              style={{
                color: tab === 'props' ? 'var(--jarvis-primary-text)' : 'var(--jarvis-subtle)',
                borderBottom: tab === 'props' ? '2px solid var(--jarvis-primary-hover)' : '2px solid transparent',
              }}
              onClick={() => setTab('props')}
            >
              Properties
            </button>
            <button
              className='flex-1 px-1.5 py-2.5 text-center font-sans text-[11.5px] font-medium cursor-pointer bg-none border-none transition-all duration-200'
              style={{
                color: tab === 'hist' ? 'var(--jarvis-primary-text)' : 'var(--jarvis-subtle)',
                borderBottom: tab === 'hist' ? '2px solid var(--jarvis-primary-hover)' : '2px solid transparent',
              }}
              onClick={() => setTab('hist')}
            >
              Run history
            </button>
          </div>
        )}

        {!collapsed && (
          <div className='flex-1 overflow-y-auto'>
            <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}`}</style>

            {tab === 'props' && (
              <>
                <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
                  <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
                    Node
                  </div>
                  <div className='mb-2'>
                    <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Title</label>
                    <input
                      className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-sans text-xs px-2 py-1.5 outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)]'
                      value={nodeData?.label || ''}
                      onChange={e => onNodeDataChange?.(selectedNode.id, { label: e.target.value })}
                    />
                  </div>
                </div>

                {['gate', 'cond', 'router', 'loop'].includes(t ?? '') && (
                  <LogicProps
                    node={selectedNode as Node<NodeData>}
                    nodes={nodes}
                    edges={edges}
                    upstreamSchema={upstreamSchema}
                    sourceLabel={sourceLabel}
                    onNodeDataChange={onNodeDataChange}
                  />
                )}
                {t === 'parallel' && (
                  <ParallelProps
                    node={selectedNode as Node<NodeData>}
                    onNodeDataChange={onNodeDataChange}
                    onParallelBranchesChange={onParallelBranchesChange}
                  />
                )}
                {t === 'pool' && (
                  <PoolProps node={selectedNode as Node<NodeData>} onNodeDataChange={onNodeDataChange} onOpenAgentPicker={onOpenAgentPicker} />
                )}
              </>
            )}

            {tab === 'hist' && (
              <RunHistory
                workflowId={workflowId}
                selectedNodeId={selectedNode.id}
                selectedNodeLabel={nodeData?.label}
              />
            )}
          </div>
        )}

        {!collapsed && selectedNode && (
          <div className='px-4 py-3 border-t border-[var(--jarvis-border)] shrink-0'>
            <button
              onClick={() => onDeleteNode?.(selectedNode.id)}
              className='w-full inline-flex items-center justify-center gap-2 px-4 py-2 border border-[var(--jarvis-border)] rounded-md shadow-sm text-sm font-medium text-[var(--jarvis-danger-text)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-danger-soft)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--jarvis-danger)] transition-colors'
            >
              <TrashIcon className='h-4 w-4' />
              Delete node
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default PropsPanel;
