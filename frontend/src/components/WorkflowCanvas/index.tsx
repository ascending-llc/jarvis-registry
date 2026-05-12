// @ts-nocheck
import './styles/canvas.css';
import './styles/nodes.css';
import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react';
import dagre from '@dagrejs/dagre';
import { useCallback, useRef, useState } from 'react';
import { useTheme } from '@/contexts/ThemeContext';
import { nodeTypes } from './components/nodes/index';
import PropsPanel from './components/panels/PropsPanel';
import NodePicker from './components/picker/NodePicker';

/* ── Edge defaults ── */
const EDGE = {
  markerEnd: { type: MarkerType.ArrowClosed, color: '#7c3aed' },
  style: { stroke: 'rgba(124,58,237,0.55)', strokeWidth: 1.5 },
};
const DASHED = { style: { stroke: 'rgba(124,58,237,0.2)', strokeWidth: 1.5, strokeDasharray: '5,5' } };

/* ── Counter for stable IDs ── */
let _id = 10;
const uid = () => `n${++_id}`;
const euid = () => `e${++_id}`;

/* ── Node dimensions ── */
const NODE_W = 220;
const NODE_H_DEFAULT = 90;
const HANDLE_SPACING_PX = 36; // must match nodes/index.tsx

function estimateNodeHeight(type: string, data: Record<string, unknown>): number {
  if (type === 'add') return 72;
  if (type === 'parallel') {
    const N = Array.isArray(data.branches) ? (data.branches as unknown[]).length : 2;
    return Math.max(80, (N - 1) * HANDLE_SPACING_PX + 60) + 50;
  }
  if (type === 'router') {
    const N = Array.isArray(data.cases) ? (data.cases as unknown[]).length : 2;
    return Math.max(80, (N - 1) * HANDLE_SPACING_PX + 60) + 50;
  }
  if (type === 'cond') return HANDLE_SPACING_PX + 60 + 50;
  return NODE_H_DEFAULT;
}

/* ── Dagre layout engine (LR direction) ── */
function getLayoutedElements(nodes, edges) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', ranksep: 100, nodesep: 40, marginx: 40, marginy: 40 });

  nodes.forEach(node => {
    g.setNode(node.id, { width: NODE_W, height: estimateNodeHeight(node.type, node.data ?? {}) });
  });
  edges.forEach(edge => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  const layoutedNodes = nodes.map(node => {
    const pos = g.node(node.id);
    const h = estimateNodeHeight(node.type, node.data ?? {});
    return {
      ...node,
      targetPosition: 'left',
      sourcePosition: 'right',
      position: { x: pos.x - NODE_W / 2, y: pos.y - h / 2 },
    };
  });

  /* ── Post-process: propagate Y alignment in topological order ────────────
   * After dagre determines X ranks, we override Y so that:
   *   • Children of multi-output nodes (cond/parallel/router) are spaced
   *     evenly and centred on the source, preventing overlap.
   *   • Children of single-output nodes inherit their parent's centre Y,
   *     keeping edges horizontal and preventing the "criss-cross" issue.
   * Processing in topo order ensures every node's Y is final before its
   * children are adjusted.                                               */

  // Minimum vertical gap between adjacent branch children (center-to-center)
  const BRANCH_CANVAS_SPACING = NODE_H_DEFAULT + 50; // 140 px

  // Build adjacency + in-degree for Kahn topo sort
  const inDeg = new Map<string, number>(layoutedNodes.map(n => [n.id, 0]));
  const adj  = new Map<string, string[]>(layoutedNodes.map(n => [n.id, []]));
  for (const e of edges) {
    if (inDeg.has(e.target)) inDeg.set(e.target, (inDeg.get(e.target) ?? 0) + 1);
    if (adj.has(e.source)) { const list = adj.get(e.source); if (list) list.push(e.target); }
  }
  const queue = [...inDeg.entries()].filter(([, d]) => d === 0).map(([id]) => id);
  const topoOrder: string[] = [];
  while (queue.length) {
    const id = queue.shift() ?? '';
    topoOrder.push(id);
    for (const next of (adj.get(id) ?? [])) {
      const d = (inDeg.get(next) ?? 1) - 1;
      inDeg.set(next, d);
      if (d === 0) queue.push(next);
    }
  }

  const nodeMap = new Map(layoutedNodes.map(n => [n.id, { ...n }]));

  for (const nodeId of topoOrder) {
    const node = nodeMap.get(nodeId);
    if (!node) continue;

    const srcH = estimateNodeHeight(node.type, node.data ?? {});
    const srcCenterY = node.position.y + srcH / 2;

    // Determine named output handles for fan-out node types
    let handleIds: string[] = [];
    if (node.type === 'cond') {
      handleIds = ['true', 'false'];
    } else if (node.type === 'parallel') {
      const N = ((node.data?.branches as string[]) ?? ['A', 'B']).length;
      handleIds = Array.from({ length: N }, (_, i) => `branch-${i}`);
    } else if (node.type === 'router') {
      const N = ((node.data?.cases as string[]) ?? []).length;
      handleIds = Array.from({ length: N }, (_, i) => `case-${i}`);
    }

    if (handleIds.length >= 2) {
      // Fan-out: space children evenly centred on this node's Y
      const N = handleIds.length;
      handleIds.forEach((handleId, i) => {
        const edge = edges.find(e => e.source === nodeId && e.sourceHandle === handleId);
        if (!edge) return;
        const target = nodeMap.get(edge.target);
        if (!target) return;
        const tgtH = estimateNodeHeight(target.type, target.data ?? {});
        const offsetY = (i - (N - 1) / 2) * BRANCH_CANVAS_SPACING;
        nodeMap.set(edge.target, {
          ...target,
          position: { ...target.position, y: srcCenterY + offsetY - tgtH / 2 },
        });
      });
    } else {
      // Single output: pull child's centre Y to match this node's centre Y
      const outEdges = edges.filter(e => e.source === nodeId);
      if (outEdges.length === 1) {
        const target = nodeMap.get(outEdges[0].target);
        if (target) {
          const tgtH = estimateNodeHeight(target.type, target.data ?? {});
          nodeMap.set(outEdges[0].target, {
            ...target,
            position: { ...target.position, y: srcCenterY - tgtH / 2 },
          });
        }
      }
    }
  }

  return { nodes: Array.from(nodeMap.values()), edges };
}

/* ── Initial state: full demo with a Conditional wired to two branches ── */
const RAW_INIT_NODES = [
  {
    id: 'n0',
    type: 'mcp',
    position: { x: 40, y: 180 },
    data: { label: 'CloudWatch', description: 'AWS alert ingestion' },
  },
  {
    id: 'n1',
    type: 'agent',
    position: { x: 280, y: 180 },
    data: { label: 'Diagnosis Agent', description: 'Root cause analysis' },
  },
  {
    id: 'n2',
    type: 'cond',
    position: { x: 520, y: 180 },
    data: { label: 'Conditional', expression: 'session_state.confidence > 0.8' },
  },
  {
    id: 'n3',
    type: 'agent',
    position: { x: 760, y: 100 },
    data: { label: 'Remediation Agent', description: 'Executes automated fixes' },
  },
  {
    id: 'n4',
    type: 'agent',
    position: { x: 760, y: 300 },
    data: { label: 'Scorer Agent', description: 'Lead scoring' },
  },
  { id: 'add3', type: 'add', position: { x: 0, y: 0 }, data: { label: '' } },
  { id: 'add4', type: 'add', position: { x: 0, y: 0 }, data: { label: '' } },
];
const RAW_INIT_EDGES = [
  { id: 'e0-1', source: 'n0', target: 'n1', ...EDGE },
  { id: 'e1-2', source: 'n1', target: 'n2', ...EDGE },
  { id: 'e2-3-true', source: 'n2', target: 'n3', sourceHandle: 'true', ...EDGE },
  { id: 'e2-4-false', source: 'n2', target: 'n4', sourceHandle: 'false', ...EDGE },
  { id: 'e3-add', source: 'n3', target: 'add3', ...DASHED },
  { id: 'e4-add', source: 'n4', target: 'add4', ...DASHED },
];

const { nodes: INIT_NODES, edges: INIT_EDGES } = getLayoutedElements(RAW_INIT_NODES, RAW_INIT_EDGES);

/* ── Mock: /agents/{id}/schema ── */
/* In production this would be fetched from the Agno backend. */
/* Each entry simulates the API response: { input: {...}, output: {...} }  */
const AGENT_SCHEMAS = {
  /* MCP Servers */
  CloudWatch: {
    output: [
      {
        name: 'message.severity',
        type: 'string',
        desc: 'Alert severity level',
        enum: ['critical', 'high', 'medium', 'low'],
      },
      { name: 'message.score', type: 'number', desc: 'Anomaly confidence score (0-1)' },
      { name: 'message.service', type: 'string', desc: 'AWS service identifier' },
      { name: 'message.tags', type: 'list(string)', desc: 'Resource tags' },
      { name: 'message.region', type: 'string', desc: 'AWS region' },
      { name: 'message.timestamp', type: 'timestamp', desc: 'Event time (RFC 3339)' },
    ],
  },
  Slack: {
    output: [
      { name: 'message.channel', type: 'string', desc: 'Slack channel name' },
      { name: 'message.user', type: 'string', desc: 'Sender user ID' },
      { name: 'message.text', type: 'string', desc: 'Message body' },
      { name: 'message.ts', type: 'string', desc: 'Message timestamp' },
    ],
  },
  PagerDuty: {
    output: [
      { name: 'message.incident_key', type: 'string', desc: 'Unique incident key' },
      { name: 'message.severity', type: 'string', desc: 'Incident severity' },
      { name: 'message.service', type: 'string', desc: 'Affected service name' },
      { name: 'message.status', type: 'string', desc: 'Incident status' },
    ],
  },
  /* A2A Agents */
  'Diagnosis Agent': {
    output: [
      { name: 'session_state.findings', type: 'list(string)', desc: 'Identified root causes' },
      { name: 'session_state.confidence', type: 'number', desc: 'Diagnosis confidence score (0-1)' },
      { name: 'session_state.severity', type: 'string', desc: 'Assessed severity level' },
      { name: 'session_state.recommended', type: 'string', desc: 'Recommended next action' },
      { name: 'session_state.retry', type: 'bool', desc: 'Should retry diagnosis' },
    ],
  },
  'Remediation Agent': {
    output: [
      { name: 'session_state.status', type: 'string', desc: 'Remediation status' },
      { name: 'session_state.actions_taken', type: 'list(string)', desc: 'Steps executed' },
      { name: 'session_state.done', type: 'bool', desc: 'Remediation complete flag' },
      { name: 'session_state.retry', type: 'bool', desc: 'Should retry remediation' },
    ],
  },
  'Classifier Agent': {
    output: [
      { name: 'session_state.category', type: 'string', desc: 'Classified category' },
      { name: 'session_state.score', type: 'number', desc: 'Classification confidence' },
      { name: 'session_state.labels', type: 'list(string)', desc: 'All predicted labels' },
    ],
  },
  'Scorer Agent': {
    output: [
      { name: 'session_state.score', type: 'number', desc: 'Final numeric score (0-1)' },
      { name: 'session_state.tier', type: 'string', desc: 'Score tier: hot|warm|cold' },
      { name: 'session_state.approved', type: 'bool', desc: 'Passed score threshold' },
    ],
  },
};

/* ── Map picker category → node type ── */
const CATEGORY_TYPE = {
  agent: _item => 'agent',
  mcp: _item => 'mcp',
  logic: item => item.id, // gate | cond | parallel | router | loop | pool
};

/* ── Default data per logic type ── */
function defaultData(type, label, desc) {
  const base = { label, description: desc || '' };
  if (type === 'parallel') return { ...base, branches: ['Branch A', 'Branch B'] };
  if (type === 'router')
    return { ...base, cases: ['critical', 'normal'], routeBy: 'session_state.severity', defaultCase: 'low-priority' };
  if (type === 'loop') return { ...base, maxIterations: 5, exitCondition: 'session_state.done == true' };
  if (type === 'pool') return { ...base, agents: ['Classifier Agent', 'Responder Agent'] };
  if (type === 'cond') return { ...base, expression: 'session_state.score > 0.8' };
  return base;
}

export default function WorkflowCanvas() {
  const { theme } = useTheme();
  const isDark = theme === 'dark';

  const [nodes, setNodes, onNodesChange] = useNodesState(INIT_NODES);
  const [edges, setEdges, onEdgesChange] = useEdgesState(INIT_EDGES);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pendingAdd, setPendingAdd] = useState(null); // id of the + node that was clicked
  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const agentPickerCb = useRef(null); // callback(agent) from PropsPanel
  const [selectedNode, setSelected] = useState(null);
  const [panelCollapsed, setPanelCollapsed] = useState(false);

  /* ── Re-run dagre layout on current nodes/edges ── */
  const runLayout = useCallback(() => {
    const { nodes: ln, edges: le } = getLayoutedElements(nodes, edges);
    setNodes(ln);
    setEdges(le);
  }, [nodes, edges]);

  /* inject onAdd into every add node */
  const nodesWithHandlers = nodes.map(n =>
    n.type === 'add'
      ? {
          ...n,
          data: {
            ...n.data,
            onAdd: () => {
              setPendingAdd(n.id);
              setPickerOpen(true);
            },
          },
        }
      : n,
  );

  const onConnect = useCallback(params => setEdges(es => addEdge({ ...params, ...EDGE }, es)), []);

  const onNodeClick = useCallback((_, node) => {
    if (node.type === 'add') return;
    setSelected(prev => {
      const isSame = prev?.id === node.id;
      if (isSame) {
        /* Toggle sidebar when clicking the already-selected node */
        setPanelCollapsed(c => !c);
        return prev;
      }
      /* Different node: always expand and switch */
      setPanelCollapsed(false);
      return node;
    });
  }, []);

  const onPaneClick = useCallback(() => {
    setSelected(null);
    setPanelCollapsed(true);
  }, []);

  /* ── Update a node's data from PropsPanel ── */
  const onNodeDataChange = useCallback((nodeId, patch) => {
    setNodes(prev =>
      prev.map(n => (n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n)),
    );
    setSelected(prev => (prev?.id === nodeId ? { ...prev, data: { ...prev.data, ...patch } } : prev));
  }, []);

  /* ── Parallel branch add / remove — keeps Add placeholders in sync ── */
  /*
   * Uses the same spacing formula as onPick:
   *   offsetY(i, N) = (i - (N-1)/2) * BRANCH_SPACING
   * This keeps Add nodes 120px apart, centred on the Parallel node's Y.
   */
  const BRANCH_SPACING = 120;
  const onParallelBranchesChange = useCallback(
    (nodeId, prevBranches, nextBranches) => {
      const N = nextBranches.length;
      const handleOffsetY = (i) => (i - (N - 1) / 2) * BRANCH_SPACING;

      if (nextBranches.length > prevBranches.length) {
        /* ── BRANCH ADDED ── */
        const newIdx = N - 1;
        const addId = `addp_${nodeId}_b${newIdx}_${Date.now()}`;

        setNodes(prev => {
          const parallelNode = prev.find(n => n.id === nodeId);
          if (!parallelNode) return prev;
          const px = parallelNode.position.x;
          const py = parallelNode.position.y;

          /* Update data.branches + reposition existing Add nodes in one pass */
          const next = prev.map(n => {
            if (n.id === nodeId) return { ...n, data: { ...n.data, branches: nextBranches } };
            if (n.type === 'add') {
              /* Find the branch edge that targets this Add node */
              const e = edges.find(
                e => e.source === nodeId && e.target === n.id && e.sourceHandle?.startsWith('branch-'),
              );
              if (!e) return n;
              const idx = parseInt(e.sourceHandle.slice(7), 10);
              return { ...n, position: { x: px + 268, y: py + handleOffsetY(idx) } };
            }
            return n;
          });

          /* Append the new Add node */
          return [
            ...next,
            {
              id: addId,
              type: 'add',
              position: { x: px + 268, y: py + handleOffsetY(newIdx) },
              data: { label: '' },
            },
          ];
        });

        setSelected(prev =>
          prev?.id === nodeId ? { ...prev, data: { ...prev.data, branches: nextBranches } } : prev,
        );

        setEdges(prev => {
          const nextEdges = [
            ...prev,
            { id: euid(), source: nodeId, target: addId, sourceHandle: `branch-${newIdx}`, ...DASHED },
          ];
          // Re-layout after branch added (must read latest nodes from closure)
          setTimeout(() => {
            setNodes(ns => {
              const { nodes: ln, edges: le } = getLayoutedElements(ns, nextEdges);
              setEdges(le);
              return ln;
            });
          }, 0);
          return nextEdges;
        });

      } else if (nextBranches.length < prevBranches.length) {
        /* ── BRANCH REMOVED ── */
        let removedIdx = prevBranches.length - 1;
        for (let i = 0; i < nextBranches.length; i++) {
          if (prevBranches[i] !== nextBranches[i]) { removedIdx = i; break; }
        }

        /* Find edge + target Add node for the removed branch */
        const removedEdge = edges.find(
          e => e.source === nodeId && e.sourceHandle === `branch-${removedIdx}`,
        );
        const removedAddId =
          removedEdge && nodes.find(n => n.id === removedEdge.target)?.type === 'add'
            ? removedEdge.target
            : null;

        setNodes(prev => {
          const parallelNode = prev.find(n => n.id === nodeId);
          if (!parallelNode) return prev;
          const px = parallelNode.position.x;
          const py = parallelNode.position.y;

          return prev
            /* Remove the orphaned Add node */
            .filter(n => n.id !== removedAddId)
            .map(n => {
              if (n.id === nodeId) return { ...n, data: { ...n.data, branches: nextBranches } };
              if (n.type === 'add') {
                const e = edges.find(
                  e => e.source === nodeId && e.target === n.id && e.sourceHandle?.startsWith('branch-'),
                );
                if (!e) return n;
                const oldIdx = parseInt(e.sourceHandle.slice(7), 10);
                /* After removal, branch indices above removedIdx shift down by 1 */
                const newHandleIdx = oldIdx > removedIdx ? oldIdx - 1 : oldIdx;
                return { ...n, position: { x: px + 268, y: py + handleOffsetY(newHandleIdx) } };
              }
              return n;
            });
        });

        setSelected(prev =>
          prev?.id === nodeId ? { ...prev, data: { ...prev.data, branches: nextBranches } } : prev,
        );

        setEdges(prev => {
          const nextEdges = prev
            .filter(e => !(e.source === nodeId && e.sourceHandle === `branch-${removedIdx}`))
            .map(e => {
              if (e.source === nodeId && e.sourceHandle?.startsWith('branch-')) {
                const idx = parseInt(e.sourceHandle.slice(7), 10);
                if (idx > removedIdx) return { ...e, sourceHandle: `branch-${idx - 1}` };
              }
              return e;
            });
          setTimeout(() => {
            setNodes(ns => {
              const { nodes: ln, edges: le } = getLayoutedElements(ns, nextEdges);
              setEdges(le);
              return ln;
            });
          }, 0);
          return nextEdges;
        });
      }
    },
    [nodes, edges],
  );

  /* ── Add picked node to canvas ── */
  const onPick = useCallback(
    (category, item) => {
      setPickerOpen(false);
      const addNode = nodes.find(n => n.id === pendingAdd);
      if (!addNode) return;

      const nodeType = CATEGORY_TYPE[category](item);
      const newId = uid();
      const data = defaultData(nodeType, item.label, item.desc);

      const newNode = {
        id: newId,
        type: nodeType,
        position: { ...addNode.position },
        data,
      };

      /* ── Determine output handles per node type ──────────────────────── */
      /* cond: 2 handles (true / false)                                    */
      /* parallel: one per branch                                           */
      /* router: one per case                                               */
      /* everything else: single unnamed handle                             */
      const getOutputHandles = () => {
        if (nodeType === 'cond') {
          return [
            { id: 'true',  offsetY: -60 },
            { id: 'false', offsetY:  60 },
          ];
        }
        if (nodeType === 'parallel') {
          const branches = data.branches ?? ['Branch A', 'Branch B'];
          return branches.map((_, i) => ({
            id: `branch-${i}`,
            offsetY: (i - (branches.length - 1) / 2) * 120,
          }));
        }
        if (nodeType === 'router') {
          const cases = data.cases ?? ['critical', 'normal'];
          return cases.map((_, i) => ({
            id: `case-${i}`,
            offsetY: (i - (cases.length - 1) / 2) * 120,
          }));
        }
        return [{ id: '', offsetY: 0 }];
      };

      const handles = getOutputHandles();

      /* Add placeholder per output handle — position is placeholder (dagre will fix it) */
      const newAddNodes = handles.map((_, i) => ({
        id: `add${newId}_${i}`,
        type: 'add',
        position: { x: 0, y: 0 },
        data: { label: '' },
      }));

      const nextNodes = nodes
        .filter(n => n.id !== pendingAdd)
        .concat([newNode, ...newAddNodes]);

      const nextEdges = (() => {
        const without = edges.filter(e => e.target !== pendingAdd);
        const incoming = edges.find(e => e.target === pendingAdd);
        return [
          ...without,
          ...(incoming ? [{ ...incoming, id: euid(), target: newId }] : []),
          ...newAddNodes.map((addN, i) => ({
            id: euid(),
            source: newId,
            target: addN.id,
            ...(handles[i].id ? { sourceHandle: handles[i].id } : {}),
            ...DASHED,
          })),
        ];
      })();

      const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(nextNodes, nextEdges);
      setNodes(layoutedNodes);
      setEdges(layoutedEdges);

      setSelected(newNode);
      setPendingAdd(null);
    },
    [pendingAdd, nodes],
  );

  /* ── Open agent-only picker from PropsPanel ── */
  const onOpenAgentPicker = useCallback(cb => {
    agentPickerCb.current = cb;
    setAgentPickerOpen(true);
  }, []);

  /* ── Minimap node colors ── */
  const miniColor = n =>
    isDark
      ? ({
          mcp: '#38bdf8',
          agent: '#a855f7',
          gate: '#f59e0b',
          cond: '#38bdf8',
          parallel: '#14b8a6',
          router: '#ec4899',
          loop: '#fb923c',
          pool: '#a855f7',
          add: '#374151',
        })[n.type] || '#374151'
      : ({
          mcp: '#0284c7',
          agent: '#6d28d9',
          gate: '#d97706',
          cond: '#0284c7',
          parallel: '#0d9488',
          router: '#db2777',
          loop: '#ea580c',
          pool: '#6d28d9',
          add: '#d1d5db',
        })[n.type] || '#d1d5db';

  return (
    <div
      className='workflow-canvas-root'
      style={{ height: '100%', width: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
    >
      {/* ── Canvas + Panel ── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        <div style={{ flex: 1 }}>
          <ReactFlow
            nodes={nodesWithHandlers}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.35 }}
            defaultEdgeOptions={EDGE}
          >
            <Background variant={BackgroundVariant.Dots} gap={28} size={1} color={isDark ? 'rgba(42,51,68,.7)' : 'rgba(15,23,42,.18)'} />
            <Controls>
              <button
                title='Auto layout'
                onClick={runLayout}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  width: 24, height: 24, background: 'none', border: 'none',
                  cursor: 'pointer', color: 'var(--wf-text-4)',
                }}
                onMouseEnter={e => { e.currentTarget.style.color = 'var(--wf-text-1)'; }}
                onMouseLeave={e => { e.currentTarget.style.color = 'var(--wf-text-4)'; }}
              >
                ⊞
              </button>
            </Controls>
            <MiniMap nodeColor={miniColor} maskColor={isDark ? 'rgba(11,16,32,.7)' : 'rgba(241,245,249,.8)'} />
          </ReactFlow>
        </div>

        <PropsPanel
          selectedNode={selectedNode}
          nodes={nodes}
          edges={edges}
          agentSchemas={AGENT_SCHEMAS}
          onOpenAgentPicker={onOpenAgentPicker}
          onNodeDataChange={onNodeDataChange}
          onParallelBranchesChange={onParallelBranchesChange}
          collapsed={panelCollapsed}
          onCollapsedChange={setPanelCollapsed}
        />
      </div>

      {/* ── Node picker modal ── */}
      {pickerOpen && (
        <NodePicker
          onPick={onPick}
          onClose={() => {
            setPickerOpen(false);
            setPendingAdd(null);
          }}
        />
      )}

      {/* ── Agent-only picker (for Pool / Loop props) ── */}
      {agentPickerOpen && (
        <NodePicker
          agentOnly
          onPick={(_, agent) => {
            agentPickerCb.current?.(agent);
            setAgentPickerOpen(false);
          }}
          onClose={() => setAgentPickerOpen(false)}
        />
      )}
    </div>
  );
}
