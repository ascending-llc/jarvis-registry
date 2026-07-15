import type { Connection, Edge, EdgeChange, NodeChange } from '@xyflow/react';
import { addEdge, applyEdgeChanges, useEdgesState, useNodesState } from '@xyflow/react';
import { useCallback } from 'react';
import { EDGE_CONFIG } from '../constants';
import { getInitialElements } from '../fixtures';
import type { WorkflowNode } from '../types';
import { pruneInvalidRefs } from '../utils/dag';

export const useCanvasNodes = (
  initialNodes?: WorkflowNode[],
  initialEdges?: Edge[],
  onChange?: () => void,
  isReadOnly = false,
) => {
  const { nodes: mockNodes, edges: mockEdges } = getInitialElements();

  const [nodes, setNodes, baseOnNodesChange] = useNodesState<WorkflowNode>(
    (initialNodes as WorkflowNode[] | undefined) ?? mockNodes,
  );
  const [edges, setEdges] = useEdgesState(initialEdges ?? mockEdges);

  const onNodesChange = useCallback(
    (changes: NodeChange<WorkflowNode>[]) => {
      if (isReadOnly) {
        const safeChanges = changes.filter(change => change.type === 'select' || change.type === 'dimensions');
        if (safeChanges.length > 0) baseOnNodesChange(safeChanges);
        return;
      }
      const nonRemoveChanges = changes.filter(c => c.type !== 'remove');
      if (nonRemoveChanges.length > 0) {
        baseOnNodesChange(nonRemoveChanges);
      }
      if (changes.some(c => c.type !== 'select')) {
        onChange?.();
      }
    },
    [baseOnNodesChange, onChange, isReadOnly],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      if (isReadOnly) {
        const safeChanges = changes.filter(change => change.type === 'select');
        if (safeChanges.length > 0) setEdges(applyEdgeChanges(safeChanges, edges));
        return;
      }
      const nextEdges = applyEdgeChanges(changes, edges);
      setEdges(nextEdges);
      setNodes(currentNodes => pruneInvalidRefs(currentNodes, nextEdges) as WorkflowNode[]);
      if (changes.some(c => c.type !== 'select')) {
        onChange?.();
      }
    },
    [edges, setEdges, setNodes, onChange, isReadOnly],
  );

  const isValidConnection = useCallback(
    (connection: Edge | Connection): boolean => {
      if (isReadOnly) return false;
      const { source, target, sourceHandle, targetHandle } = connection;
      if (source === target) return false;

      const normalizeHandle = (h: string | null | undefined): string | null => h ?? null;

      const hasTargetEdge = edges.some(
        e => e.target === target && normalizeHandle(e.targetHandle) === normalizeHandle(targetHandle),
      );
      if (hasTargetEdge) return false;

      const hasSourceEdge = edges.some(e => {
        if (e.source !== source || normalizeHandle(e.sourceHandle) !== normalizeHandle(sourceHandle)) return false;
        const targetNode = nodes.find(n => n.id === e.target);
        return targetNode?.type !== 'add';
      });
      if (hasSourceEdge) return false;

      return true;
    },
    [edges, nodes, isReadOnly],
  );

  const onConnect = useCallback(
    (params: Connection) => {
      if (isReadOnly) return;
      if (!isValidConnection(params)) return;

      const normalizeHandle = (h: string | null | undefined): string | null => h ?? null;

      const existingAddEdge = edges.find(e => {
        if (e.source !== params.source || normalizeHandle(e.sourceHandle) !== normalizeHandle(params.sourceHandle))
          return false;
        const targetNode = nodes.find(n => n.id === e.target);
        return targetNode?.type === 'add';
      });

      if (existingAddEdge) {
        const nextEdges = addEdge(
          { ...params, ...EDGE_CONFIG },
          edges.filter(edge => edge.id !== existingAddEdge.id),
        );
        setEdges(nextEdges);
        setNodes(
          currentNodes =>
            pruneInvalidRefs(
              currentNodes.filter(node => node.id !== existingAddEdge.target),
              nextEdges,
            ) as WorkflowNode[],
        );
      } else {
        const nextEdges = addEdge({ ...params, ...EDGE_CONFIG }, edges);
        setEdges(nextEdges);
        setNodes(currentNodes => pruneInvalidRefs(currentNodes, nextEdges) as WorkflowNode[]);
      }

      onChange?.();
    },
    [isReadOnly, isValidConnection, edges, nodes, setEdges, setNodes, onChange],
  );

  return {
    nodes,
    setNodes,
    edges,
    setEdges,
    onNodesChange,
    onEdgesChange,
    onConnect,
    isValidConnection,
    mockNodes,
    mockEdges,
  };
};
