import type { Node, NodeProps } from '@xyflow/react';
import { Handle, Position, useUpdateNodeInternals } from '@xyflow/react';
import { useContext, useEffect } from 'react';
import { CanvasActionsContext } from '../CanvasView';

import './index.css';

/** Compact spacing between handles inside the node card. */
const HANDLE_SPACING_PX = 36;

interface HeaderProps {
  iconClass: string;
  iconLabel: string;
  title: string;
  dotClass: string;
}

/** Shared header for all node types. */
const Header: React.FC<HeaderProps> = ({ iconClass, iconLabel, title, dotClass }) => {
  return (
    <div className='node-header'>
      <div className={`node-icon ${iconClass}`}>{iconLabel}</div>
      <div className='node-title'>{title}</div>
      <div className={`node-dot ${dotClass}`} />
    </div>
  );
};

/** MCP Server node. */
export const McpNode: React.FC<NodeProps<Node<import('../types').McpNodeData>>> = ({ data, selected }) => {
  return (
    <div className={`node-wrap mcp ${selected ? 'selected' : ''}`}>
      <Handle type='target' position={Position.Left} />
      <Header iconClass='mcp' iconLabel='MCP' title={data.label} dotClass='mcp' />
      <div className='node-body'>{data.description}</div>
      <Handle type='source' position={Position.Right} />
    </div>
  );
};

/** A2A Agent node. */
export const AgentNode: React.FC<NodeProps<Node<import('../types').AgentNodeData>>> = ({ data, selected }) => {
  return (
    <div className={`node-wrap agent ${selected ? 'selected' : ''}`}>
      <Handle type='target' position={Position.Left} />
      <Header iconClass='agent' iconLabel='A2A' title={data.label} dotClass='agent' />
      <div className='node-body'>{data.description}</div>
      <Handle type='source' position={Position.Right} />
    </div>
  );
};

/** Approval Gate (HITL) node. */
export const GateNode: React.FC<NodeProps<Node<import('../types').GateNodeData>>> = ({ data, selected }) => {
  return (
    <div className={`node-wrap gate ${selected ? 'selected' : ''}`}>
      <Handle type='target' position={Position.Left} />
      <Header iconClass='gate' iconLabel='⏸' title={data.label || 'Approval Gate'} dotClass='gate' />
      <div className='node-body'>{data.description || 'Pause run for human review'}</div>
      <div className='node-footer'>
        <span className='node-badge gate font-mono'>⏸ awaiting human</span>
        {data.timeout && <span className='node-hint font-mono'>{data.timeout}</span>}
      </div>
      <Handle type='source' position={Position.Right} />
    </div>
  );
};

/** Conditional (if / else) node with two source handles spread vertically. */
export const CondNode: React.FC<NodeProps<Node<import('../types').CondNodeData>>> = ({ data, selected }) => {
  return (
    <div className={`node-wrap cond ${selected ? 'selected' : ''}`} style={{ minHeight: HANDLE_SPACING_PX + 60 }}>
      <Handle type='target' position={Position.Left} />
      <Header iconClass='cond' iconLabel='if' title={data.label || 'Conditional'} dotClass='cond' />
      <div className='node-body'>
        <code
          className='font-mono'
          style={{
            fontSize: 10,
            color: 'var(--jarvis-blue)',
            wordBreak: 'break-all',
          }}
        >
          {data.expression || 'session_state.score > 0.8'}
        </code>
      </div>
      <div className='node-footer'>
        <span className='node-badge blue font-mono'>true →</span>
        <span className='node-badge gray font-mono'>false →</span>
      </div>
      <Handle
        type='source'
        position={Position.Right}
        id='true'
        style={{
          top: `calc(50% - ${HANDLE_SPACING_PX / 2}px)`,
          background: 'var(--jarvis-blue)',
          borderColor: 'var(--jarvis-blue)',
        }}
      />
      <Handle
        type='source'
        position={Position.Right}
        id='false'
        style={{
          top: `calc(50% + ${HANDLE_SPACING_PX / 2}px)`,
          background: 'var(--jarvis-subtle)',
          borderColor: 'var(--jarvis-border-strong)',
        }}
      />
    </div>
  );
};

/** Parallel (fan-out) node with one source handle per branch. */
export const ParallelNode: React.FC<NodeProps<Node<import('../types').ParallelNodeData>>> = ({
  id,
  data,
  selected,
}) => {
  const branches = Array.isArray(data.branches) ? data.branches : ['Branch A', 'Branch B'];
  const updateNodeInternals = useUpdateNodeInternals();

  useEffect(() => {
    updateNodeInternals(id);
  }, [branches.length, id, updateNodeInternals]);

  const N = branches.length;
  const minHeight = Math.max(80, (N - 1) * HANDLE_SPACING_PX + 60);
  return (
    <div className={`node-wrap parallel ${selected ? 'selected' : ''}`} style={{ minHeight }}>
      <Handle type='target' position={Position.Left} />
      <Header iconClass='parallel' iconLabel='∥' title={data.label || 'Parallel'} dotClass='parallel' />
      <div className='node-body'>Fan-out — runs all branches concurrently</div>
      <div className='node-footer'>
        {branches.map((b, i) => (
          <span key={i} className='node-badge teal font-mono'>
            {b}
          </span>
        ))}
        <span className='node-hint font-mono'>+ add in props</span>
      </div>
      {branches.map((_, i) => (
        <Handle
          key={i}
          type='source'
          position={Position.Right}
          id={`branch-${i}`}
          style={{
            top: `calc(50% + ${(i - (N - 1) / 2) * HANDLE_SPACING_PX}px)`,
            background: 'var(--jarvis-teal)',
            borderColor: 'var(--jarvis-teal)',
          }}
        />
      ))}
    </div>
  );
};

/** Router (switch / case) node. */
export const RouterNode: React.FC<NodeProps<Node<import('../types').RouterNodeData>>> = ({ id, data, selected }) => {
  const cases = Array.isArray(data.cases) ? data.cases : ['critical', 'normal'];
  const updateNodeInternals = useUpdateNodeInternals();

  useEffect(() => {
    updateNodeInternals(id);
  }, [cases.length, id, updateNodeInternals]);

  const N = cases.length;
  const minHeight = Math.max(80, (N - 1) * HANDLE_SPACING_PX + 60);
  return (
    <div className={`node-wrap router ${selected ? 'selected' : ''}`} style={{ minHeight }}>
      <Handle type='target' position={Position.Left} />
      <Header iconClass='router' iconLabel='⇄' title={data.label || 'Router'} dotClass='router' />
      <div className='node-body'>
        <span className='font-mono' style={{ fontSize: 10, color: 'var(--jarvis-subtle)' }}>
          switch {data.routeBy || 'session_state.severity'}
        </span>
      </div>
      <div className='node-footer'>
        {cases.map((c, i) => (
          <span key={i} className='node-badge pink font-mono'>
            case: {c}
          </span>
        ))}
        {data.defaultCase && <span className='node-badge gray font-mono'>default: {data.defaultCase}</span>}
      </div>
      {cases.map((_, i) => (
        <Handle
          key={i}
          type='source'
          position={Position.Right}
          id={`case-${i}`}
          style={{
            top: `calc(50% + ${(i - (N - 1) / 2) * HANDLE_SPACING_PX}px)`,
            background: 'var(--jarvis-pink)',
            borderColor: 'var(--jarvis-pink)',
          }}
        />
      ))}
    </div>
  );
};

/** Loop node with back-edge handle. */
export const LoopNode: React.FC<NodeProps<Node<import('../types').LoopNodeData>>> = ({ data, selected }) => {
  return (
    <div className={`node-wrap loop ${selected ? 'selected' : ''}`}>
      <Handle type='target' position={Position.Left} />
      {/* Back-edge comes in from bottom */}
      <Handle
        type='target'
        position={Position.Bottom}
        id='back'
        style={{ background: 'var(--jarvis-orange)', borderColor: 'var(--jarvis-orange)' }}
      />
      <Header iconClass='loop' iconLabel='↻' title={data.label || 'Loop'} dotClass='loop' />
      <div className='node-body'>
        Max <strong style={{ color: 'var(--jarvis-orange)' }}>{data.maxIterations || 5}</strong> iterations
        {data.exitCondition && (
          <div style={{ marginTop: 3 }}>
            <code className='font-mono' style={{ fontSize: 10, color: 'var(--jarvis-subtle)' }}>
              exit: {data.exitCondition}
            </code>
          </div>
        )}
      </div>
      <div className='node-footer'>
        <span className='node-badge orange font-mono'>↻ body →</span>
        <span className='node-badge gray font-mono'>exit ↓</span>
      </div>
      <Handle
        type='source'
        position={Position.Right}
        id='body'
        style={{ background: 'var(--jarvis-orange)', borderColor: 'var(--jarvis-orange)' }}
      />
      <Handle
        type='source'
        position={Position.Bottom}
        id='exit'
        style={{ background: 'var(--jarvis-subtle)', borderColor: 'var(--jarvis-border-strong)' }}
      />
    </div>
  );
};

/** Agent Pool node. */
export const PoolNode: React.FC<NodeProps<Node<import('../types').PoolNodeData>>> = ({ data, selected }) => {
  const agents = Array.isArray(data.agents) ? data.agents : [];
  const remaining = 5 - agents.length;
  return (
    <div className={`node-wrap pool ${selected ? 'selected' : ''}`}>
      <Handle type='target' position={Position.Left} />
      <Header iconClass='pool' iconLabel='◈' title={data.label || 'Agent Pool'} dotClass='pool' />
      <div className='node-body'>LLM selects best-fit agent at runtime</div>
      <div className='node-footer'>
        {agents.map((a, i) => (
          <span key={i} className='node-badge purple font-mono'>
            {typeof a === 'string' ? a : a?.label}
          </span>
        ))}
        {remaining > 0 && <span className='node-hint font-mono'>+ add up to {remaining}</span>}
      </div>
      <Handle type='source' position={Position.Right} />
    </div>
  );
};

/** Add-node placeholder for inserting new nodes. */
export const AddNode: React.FC<NodeProps<Node<import('../types').BaseNodeData>>> = ({ id }) => {
  const { onAdd } = useContext(CanvasActionsContext);
  return (
    <>
      <Handle type='target' position={Position.Left} />
      <div className='add-node'>
        <div
          className='add-plus cursor-pointer'
          onClick={e => {
            e.stopPropagation();
            onAdd?.(id);
          }}
        >
          +
        </div>
        <div className='add-label'>Add next node</div>
      </div>
    </>
  );
};

/** Node types map for ReactFlow. */
export const nodeTypes = {
  mcp: McpNode,
  agent: AgentNode,
  gate: GateNode,
  cond: CondNode,
  parallel: ParallelNode,
  router: RouterNode,
  loop: LoopNode,
  pool: PoolNode,
  add: AddNode,
};
