import type { Connection, Edge, Node } from '@xyflow/react';
import { addEdge, useEdgesState, useNodesState } from '@xyflow/react';
import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ADD_NODE_MARGIN_X, BRANCH_SPACING, DASHED_EDGE, EDGE_CONFIG, NODE_WIDTH } from './constants';
import { getInitialElements } from './fixtures';
import { estimateNodeHeight, getLayoutedElements } from './layout';
import type { AgentInfo, LogicStep, NodeData, PickerItem, WorkflowNode } from './types';

const CATEGORY_TYPE: Record<string, (item: PickerItem | LogicStep) => string> = {
  agent: _item => 'agent',
  mcp: _item => 'mcp',
  logic: item => item.id,
};

const getDefaultNodeData = (type: string, label: string, desc: string): NodeData => {
  const base = { label, description: desc || '' };
  if (type === 'parallel') return { ...base, branches: ['Branch A', 'Branch B'] };
  if (type === 'router')
    return { ...base, cases: ['critical', 'normal'], routeBy: 'session_state.severity', defaultCase: 'low-priority' };
  if (type === 'loop') return { ...base, maxIterations: 5, exitCondition: 'session_state.done == true' };
  if (type === 'pool')
    return {
      ...base,
      agents: [
        { id: 'classifier-agent', label: 'Classifier Agent', desc: '' },
        { id: 'responder-agent', label: 'Responder Agent', desc: '' },
      ] satisfies AgentInfo[],
    };
  if (type === 'cond') return { ...base, expression: 'session_state.score > 0.8' };
  return base;
};

export const useWorkflowCanvas = (initialNodes?: Node[], initialEdges?: Edge[]) => {
  const { nodes: mockNodes, edges: mockEdges } = getInitialElements();

  const [nodes, setNodes, onNodesChange] = useNodesState<WorkflowNode>(
    (initialNodes as WorkflowNode[] | undefined) ?? mockNodes,
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges ?? mockEdges);

  const nodeIdRef = useRef(0);
  const edgeIdRef = useRef(0);

  /** Keep counters ahead of existing canvas ids so new nodes never reuse n1, n2, … */
  const syncIdCounters = useCallback((currentNodes: Node[], currentEdges: Edge[]) => {
    let maxNode = nodeIdRef.current;
    for (const n of currentNodes) {
      const m = /^n(\d+)$/.exec(n.id);
      if (m) maxNode = Math.max(maxNode, Number.parseInt(m[1], 10));
      const addM = /^addn(\d+)_/.exec(n.id);
      if (addM) maxNode = Math.max(maxNode, Number.parseInt(addM[1], 10));
    }
    nodeIdRef.current = maxNode;

    let maxEdge = edgeIdRef.current;
    for (const e of currentEdges) {
      const m = /^e(\d+)$/.exec(e.id);
      if (m) maxEdge = Math.max(maxEdge, Number.parseInt(m[1], 10));
    }
    edgeIdRef.current = maxEdge;
  }, []);

  useEffect(() => {
    const seedNodes = (initialNodes as WorkflowNode[] | undefined) ?? mockNodes;
    const seedEdges = initialEdges ?? mockEdges;
    syncIdCounters(seedNodes, seedEdges);
  }, [initialNodes, initialEdges, mockNodes, mockEdges, syncIdCounters]);

  const generateNodeId = () => `n${++nodeIdRef.current}`;
  const generateEdgeId = () => `e${++edgeIdRef.current}`;

  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerTab, setPickerTab] = useState('A2A Agents');
  const [pendingAdd, setPendingAdd] = useState<string | null>(null);
  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const agentPickerCb = useRef<((agent: AgentInfo) => void) | null>(null);

  const [selectedNode, setSelected] = useState<WorkflowNode | null>(null);
  const [panelCollapsed, setPanelCollapsed] = useState(false);

  const runLayout = useCallback(() => {
    const { nodes: ln, edges: le } = getLayoutedElements(nodes, edges);
    setNodes(ln);
    setEdges(le);
  }, [nodes, edges, setNodes, setEdges]);

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

  const isValidConnection = useCallback(
    (connection: Edge | Connection): boolean => {
      const { source, target, sourceHandle, targetHandle } = connection;

      if (source === target) return false;

      const normalizeHandle = (h: string | null | undefined): string | null => h ?? null;

      const hasTargetEdge = edges.some(
        e => e.target === target && normalizeHandle(e.targetHandle) === normalizeHandle(targetHandle),
      );
      if (hasTargetEdge) return false;

      const hasSourceEdge = edges.some(
        e => e.source === source && normalizeHandle(e.sourceHandle) === normalizeHandle(sourceHandle),
      );
      if (hasSourceEdge) return false;

      return true;
    },
    [edges],
  );

  const onConnect = useCallback(
    (params: Connection) => {
      if (!isValidConnection(params)) return;
      setEdges(es => addEdge({ ...params, ...EDGE_CONFIG }, es));
    },
    [isValidConnection, setEdges],
  );

  const onNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    if (node.type === 'add') return;
    const workflowNode = node as WorkflowNode;
    setSelected(prev => {
      const isSame = prev?.id === workflowNode.id;
      if (isSame) {
        setPanelCollapsed(c => !c);
        return prev;
      }
      setPanelCollapsed(false);
      return workflowNode;
    });
  }, []);

  const onPaneClick = useCallback(() => {
    setSelected(null);
    setPanelCollapsed(true);
  }, []);

  const onNodeDataChange = useCallback(
    (nodeId: string, patch: Partial<NodeData>) => {
      setNodes(prev => prev.map(n => (n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n)));
      setSelected(prev => (prev?.id === nodeId ? { ...prev, data: { ...prev.data, ...patch } } : prev));
    },
    [setNodes],
  );

  const onDeleteNode = useCallback(
    (nodeId: string) => {
      setNodes(prev => prev.filter(n => n.id !== nodeId));
      setEdges(prev => prev.filter(e => e.source !== nodeId && e.target !== nodeId));
      setSelected(null);
      setPanelCollapsed(true);
    },
    [setNodes, setEdges],
  );

  const onParallelBranchesChange = useCallback(
    (nodeId: string, prevBranches: string[], nextBranches: string[]) => {
      const N = nextBranches.length;
      const handleOffsetY = (i: number): number => (i - (N - 1) / 2) * BRANCH_SPACING;

      syncIdCounters(nodes, edges);

      if (nextBranches.length > prevBranches.length) {
        const newIdx = N - 1;
        const addId = `addp_${nodeId}_b${newIdx}_${Date.now()}`;

        const parallelNode = nodes.find(n => n.id === nodeId);
        if (!parallelNode) return;
        const px = parallelNode.position.x;
        const py = parallelNode.position.y;

        const nextNodes = nodes
          .map(n => {
            if (n.id === nodeId) return { ...n, data: { ...n.data, branches: nextBranches } };
            if (n.type === 'add') {
              const e = edges.find(
                e => e.source === nodeId && e.target === n.id && e.sourceHandle?.startsWith('branch-'),
              );
              if (!e || !e.sourceHandle) return n;
              const idx = parseInt(e.sourceHandle.slice(7), 10);
              return { ...n, position: { x: px + NODE_WIDTH + ADD_NODE_MARGIN_X, y: py + handleOffsetY(idx) } };
            }
            return n;
          })
          .concat([
            {
              id: addId,
              type: 'add',
              position: { x: px + NODE_WIDTH + ADD_NODE_MARGIN_X, y: py + handleOffsetY(newIdx) },
              data: { label: '' },
            },
          ]);

        const nextEdges = [
          ...edges,
          { id: generateEdgeId(), source: nodeId, target: addId, sourceHandle: `branch-${newIdx}`, ...DASHED_EDGE },
        ];

        const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(nextNodes, nextEdges);
        setNodes(layoutedNodes);
        setEdges(layoutedEdges);
        setSelected(prev => (prev?.id === nodeId ? { ...prev, data: { ...prev.data, branches: nextBranches } } : prev));
      } else if (nextBranches.length < prevBranches.length) {
        let removedIdx = prevBranches.length - 1;
        for (let i = 0; i < nextBranches.length; i++) {
          if (prevBranches[i] !== nextBranches[i]) {
            removedIdx = i;
            break;
          }
        }

        const removedEdge = edges.find(e => e.source === nodeId && e.sourceHandle === `branch-${removedIdx}`);
        const removedAddId =
          removedEdge && nodes.find(n => n.id === removedEdge.target)?.type === 'add' ? removedEdge.target : null;

        const parallelNode = nodes.find(n => n.id === nodeId);
        if (!parallelNode) return;
        const px = parallelNode.position.x;
        const py = parallelNode.position.y;

        const nextNodes = nodes
          .filter(n => n.id !== removedAddId)
          .map(n => {
            if (n.id === nodeId) return { ...n, data: { ...n.data, branches: nextBranches } };
            if (n.type === 'add') {
              const e = edges.find(
                e => e.source === nodeId && e.target === n.id && e.sourceHandle?.startsWith('branch-'),
              );
              if (!e || !e.sourceHandle) return n;
              const oldIdx = parseInt(e.sourceHandle.slice(7), 10);
              const newHandleIdx = oldIdx > removedIdx ? oldIdx - 1 : oldIdx;
              return {
                ...n,
                position: { x: px + NODE_WIDTH + ADD_NODE_MARGIN_X, y: py + handleOffsetY(newHandleIdx) },
              };
            }
            return n;
          });

        const nextEdges = edges
          .filter(e => !(e.source === nodeId && e.sourceHandle === `branch-${removedIdx}`))
          .map(e => {
            if (e.source === nodeId && e.sourceHandle?.startsWith('branch-')) {
              const idx = parseInt(e.sourceHandle.slice(7), 10);
              if (idx > removedIdx) return { ...e, sourceHandle: `branch-${idx - 1}` };
            }
            return e;
          });

        const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(nextNodes, nextEdges);
        setNodes(layoutedNodes);
        setEdges(layoutedEdges);
        setSelected(prev => (prev?.id === nodeId ? { ...prev, data: { ...prev.data, branches: nextBranches } } : prev));
      }
    },
    [nodes, edges, setNodes, setEdges, syncIdCounters],
  );

  const onPick = useCallback(
    (category: 'agent' | 'mcp' | 'logic', item: PickerItem | LogicStep) => {
      setPickerOpen(false);
      const addNode = nodes.find(n => n.id === pendingAdd);
      if (!addNode) return;

      syncIdCounters(nodes, edges);

      const nodeType = CATEGORY_TYPE[category](item);
      const newId = generateNodeId();
      const data = getDefaultNodeData(nodeType, item.label, item.desc);

      const newNode: WorkflowNode = {
        id: newId,
        type: nodeType,
        position: { ...addNode.position },
        data,
      };

      const getOutputHandles = () => {
        if (nodeType === 'cond') {
          return [
            { id: 'true', offsetY: -60 },
            { id: 'false', offsetY: 60 },
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
      const newH = estimateNodeHeight(nodeType, data);

      const newAddNodes = handles.map((handle, i) => ({
        id: `add${newId}_${i}`,
        type: 'add',
        position: {
          x: newNode.position.x + NODE_WIDTH + ADD_NODE_MARGIN_X,
          y: newNode.position.y + newH / 2 + handle.offsetY - 36,
        },
        data: { label: '' },
      }));

      const nextNodes = nodes.filter(n => n.id !== pendingAdd).concat([newNode, ...newAddNodes]);

      const nextEdges = (() => {
        const without = edges.filter(e => e.target !== pendingAdd);
        const incoming = edges.find(e => e.target === pendingAdd);
        return [
          ...without,
          ...(incoming ? [{ ...incoming, id: generateEdgeId(), target: newId }] : []),
          ...newAddNodes.map((addN, i) => ({
            id: generateEdgeId(),
            source: newId,
            target: addN.id,
            ...(handles[i].id ? { sourceHandle: handles[i].id } : {}),
            ...DASHED_EDGE,
          })),
        ];
      })();

      const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
        nextNodes as WorkflowNode[],
        nextEdges,
      );
      setNodes(layoutedNodes);
      setEdges(layoutedEdges);

      setSelected(newNode);
      setPendingAdd(null);
    },
    [pendingAdd, nodes, edges, setNodes, setEdges, syncIdCounters],
  );

  const onOpenAgentPicker = useCallback((cb: (agent: AgentInfo) => void) => {
    agentPickerCb.current = cb;
    setAgentPickerOpen(true);
  }, []);

  return {
    nodes,
    edges,
    nodesWithHandlers,
    pickerOpen,
    pickerTab,
    agentPickerOpen,
    selectedNode,
    panelCollapsed,
    runLayout,
    onNodesChange,
    onEdgesChange,
    onConnect,
    onNodeClick,
    onPaneClick,
    onNodeDataChange,
    onDeleteNode,
    onParallelBranchesChange,
    onPick,
    onOpenAgentPicker,
    isValidConnection,
    setPickerOpen,
    setPickerTab,
    setAgentPickerOpen,
    setPendingAdd,
    setPanelCollapsed,
    agentPickerCb,
  };
};
