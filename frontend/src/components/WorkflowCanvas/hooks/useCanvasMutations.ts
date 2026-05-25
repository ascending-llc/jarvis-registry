import type { Edge, Node } from '@xyflow/react';
import { useCallback } from 'react';
import { ADD_NODE_MARGIN_X, BRANCH_SPACING, DASHED_EDGE, NODE_WIDTH } from '../constants';
import { estimateNodeHeight, getLayoutedElements } from '../layout';
import type { AgentInfo, LogicStep, NodeData, PickerItem, WorkflowNode } from '../types';

const CATEGORY_TYPE: Record<string, (item: PickerItem | LogicStep) => string> = {
  agent: _item => 'agent',
  mcp: _item => 'mcp',
  logic: item => item.id,
};

const getDefaultNodeData = (type: string, label: string, desc: string): NodeData => {
  const base = { label, description: desc || '' };
  if (type === 'parallel') {
    const pData = base as import('../types').ParallelNodeData;
    return { ...base, branches: pData.branches || ['Branch A', 'Branch B'] };
  }
  if (type === 'router') {
    const rData = base as import('../types').RouterNodeData;
    return {
      ...base,
      cases: rData.cases || ['critical', 'normal'],
      routeBy: 'session_state.severity',
      defaultCase: 'low-priority',
    };
  }
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
  return base as NodeData;
};

export const useCanvasMutations = ({
  nodes,
  edges,
  setNodes,
  setEdges,
  setSelected,
  setPanelCollapsed,
  syncIdCounters,
  generateNodeId,
  generateEdgeId,
  onChange,
}: {
  nodes: WorkflowNode[];
  edges: Edge[];
  setNodes: React.Dispatch<React.SetStateAction<WorkflowNode[]>>;
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>;
  setSelected: React.Dispatch<React.SetStateAction<WorkflowNode | null>>;
  setPanelCollapsed: React.Dispatch<React.SetStateAction<boolean>>;
  syncIdCounters: (nodes: Node[], edges: Edge[]) => void;
  generateNodeId: () => string;
  generateEdgeId: () => string;
  onChange?: () => void;
}) => {
  const onNodeDataChange = useCallback(
    (nodeId: string, patch: Partial<NodeData>) => {
      setNodes(prev => prev.map(n => (n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n)));
      setSelected(prev => (prev?.id === nodeId ? { ...prev, data: { ...prev.data, ...patch } } : prev));
      onChange?.();
    },
    [setNodes, setSelected, onChange],
  );

  const onDeleteNode = useCallback(
    (nodeId: string) => {
      setNodes(prev => prev.filter(n => n.id !== nodeId));
      setEdges(prev => prev.filter(e => e.source !== nodeId && e.target !== nodeId));
      setSelected(null);
      setPanelCollapsed(true);
      onChange?.();
    },
    [setNodes, setEdges, setSelected, setPanelCollapsed, onChange],
  );

  const onDynamicBranchesChange = useCallback(
    (
      nodeId: string,
      prevBranches: string[],
      nextBranches: string[],
      options: { dataKey: 'branches' | 'cases'; handlePrefix: string },
    ) => {
      const { dataKey, handlePrefix } = options;
      if (prevBranches === nextBranches) return;
      const N = nextBranches.length;
      const handleOffsetY = (i: number): number => (i - (N - 1) / 2) * BRANCH_SPACING;

      syncIdCounters(nodes, edges);
      onChange?.();

      if (nextBranches.length > prevBranches.length) {
        const newIdx = N - 1;
        const addId = `addp_${nodeId}_b${newIdx}_${Date.now()}`;

        const parallelNode = nodes.find(n => n.id === nodeId);
        if (!parallelNode) return;
        const px = parallelNode.position.x;
        const py = parallelNode.position.y;

        const nextNodes = nodes
          .map(n => {
            if (n.id === nodeId) return { ...n, data: { ...n.data, [dataKey]: nextBranches } };
            if (n.type === 'add') {
              const e = edges.find(
                e => e.source === nodeId && e.target === n.id && e.sourceHandle?.startsWith(`${handlePrefix}-`),
              );
              if (!e || !e.sourceHandle) return n;
              const idx = parseInt(e.sourceHandle.split('-')[1], 10);
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
          { id: generateEdgeId(), source: nodeId, target: addId, sourceHandle: `${handlePrefix}-${newIdx}`, ...DASHED_EDGE },
        ];

        setNodes(nextNodes as WorkflowNode[]);
        setEdges(nextEdges);
        setSelected(prev => (prev?.id === nodeId ? { ...prev, data: { ...prev.data, [dataKey]: nextBranches } } : prev));
      } else if (nextBranches.length < prevBranches.length) {
        let removedIdx = prevBranches.length - 1;
        for (let i = 0; i < nextBranches.length; i++) {
          if (prevBranches[i] !== nextBranches[i]) {
            removedIdx = i;
            break;
          }
        }

        const removedEdge = edges.find(e => e.source === nodeId && e.sourceHandle === `${handlePrefix}-${removedIdx}`);
        const removedAddId =
          removedEdge && nodes.find(n => n.id === removedEdge.target)?.type === 'add' ? removedEdge.target : null;

        const parallelNode = nodes.find(n => n.id === nodeId);
        if (!parallelNode) return;
        const px = parallelNode.position.x;
        const py = parallelNode.position.y;

        const nextNodes = nodes
          .filter(n => n.id !== removedAddId)
          .map(n => {
            if (n.id === nodeId) return { ...n, data: { ...n.data, [dataKey]: nextBranches } };
            if (n.type === 'add') {
              const e = edges.find(
                e => e.source === nodeId && e.target === n.id && e.sourceHandle?.startsWith(`${handlePrefix}-`),
              );
              if (!e || !e.sourceHandle) return n;
              const oldIdx = parseInt(e.sourceHandle.split('-')[1], 10);
              const newHandleIdx = oldIdx > removedIdx ? oldIdx - 1 : oldIdx;
              return {
                ...n,
                position: { x: px + NODE_WIDTH + ADD_NODE_MARGIN_X, y: py + handleOffsetY(newHandleIdx) },
              };
            }
            return n;
          });

        const nextEdges = edges
          .filter(e => !(e.source === nodeId && e.sourceHandle === `${handlePrefix}-${removedIdx}`))
          .map(e => {
            if (e.source === nodeId && e.sourceHandle?.startsWith(`${handlePrefix}-`)) {
              const idx = parseInt(e.sourceHandle.split('-')[1], 10);
              if (idx > removedIdx) return { ...e, sourceHandle: `${handlePrefix}-${idx - 1}` };
            }
            return e;
          });

        const safeNodes = nextNodes as WorkflowNode[];
        setNodes(safeNodes);
        setEdges(nextEdges);
        setSelected(prev => (prev?.id === nodeId ? { ...prev, data: { ...prev.data, [dataKey]: nextBranches } } : prev));
      }
    },
    [nodes, edges, setNodes, setEdges, setSelected, syncIdCounters, generateEdgeId, onChange],
  );

  const onParallelBranchesChange = useCallback(
    (nodeId: string, prevBranches: string[], nextBranches: string[]) => {
      onDynamicBranchesChange(nodeId, prevBranches, nextBranches, { dataKey: 'branches', handlePrefix: 'branch' });
    },
    [onDynamicBranchesChange],
  );

  const onRouterCasesChange = useCallback(
    (nodeId: string, prevCases: string[], nextCases: string[]) => {
      onDynamicBranchesChange(nodeId, prevCases, nextCases, { dataKey: 'cases', handlePrefix: 'case' });
    },
    [onDynamicBranchesChange],
  );

  const onPick = useCallback(
    (pendingAddId: string, category: 'agent' | 'mcp' | 'logic', item: PickerItem | LogicStep) => {
      const addNode = nodes.find(n => n.id === pendingAddId);
      if (!addNode) return;

      syncIdCounters(nodes, edges);
      onChange?.();

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
          const pData = data as import('../types').ParallelNodeData;
          const branches = pData.branches ?? ['Branch A', 'Branch B'];
          return branches.map((_, i) => ({
            id: `branch-${i}`,
            offsetY: (i - (branches.length - 1) / 2) * 120,
          }));
        }
        if (nodeType === 'router') {
          const rData = data as import('../types').RouterNodeData;
          const cases = rData.cases ?? ['critical', 'normal'];
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

      const nextNodes = nodes.filter(n => n.id !== pendingAddId).concat([newNode, ...newAddNodes]);

      const nextEdges = (() => {
        const without = edges.filter(e => e.target !== pendingAddId);
        const incoming = edges.find(e => e.target === pendingAddId);
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

      setNodes(nextNodes as WorkflowNode[]);
      setEdges(nextEdges);

      setSelected(newNode);
    },
    [nodes, edges, setNodes, setEdges, setSelected, syncIdCounters, generateNodeId, generateEdgeId, onChange],
  );

  return {
    onNodeDataChange,
    onDeleteNode,
    onParallelBranchesChange,
    onRouterCasesChange,
    onPick,
  };
};
