import type { Connection, Edge, EdgeChange, Node, NodeChange } from '@xyflow/react';
import { addEdge, useEdgesState, useNodesState } from '@xyflow/react';
import { useCallback } from 'react';
import { EDGE_CONFIG } from '../constants';
import { getInitialElements } from '../fixtures';
import type { WorkflowNode } from '../types';

export const useCanvasNodes = (initialNodes?: Node[], initialEdges?: Edge[], onChange?: () => void) => {
  const { nodes: mockNodes, edges: mockEdges } = getInitialElements();

  const [nodes, setNodes, baseOnNodesChange] = useNodesState<WorkflowNode>(
    (initialNodes as WorkflowNode[] | undefined) ?? mockNodes,
  );
  const [edges, setEdges, baseOnEdgesChange] = useEdgesState(initialEdges ?? mockEdges);

  const onNodesChange = useCallback(
    (changes: NodeChange<WorkflowNode>[]) => {
      const nonRemoveChanges = changes.filter(c => c.type !== 'remove');
      if (nonRemoveChanges.length > 0) {
        baseOnNodesChange(nonRemoveChanges);
      }
      if (changes.some(c => c.type !== 'select')) {
        onChange?.();
      }
    },
    [baseOnNodesChange, onChange],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      baseOnEdgesChange(changes);
      if (changes.some(c => c.type !== 'select')) {
        onChange?.();
      }
    },
    [baseOnEdgesChange, onChange],
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

      const hasSourceEdge = edges.some(e => {
        if (e.source !== source || normalizeHandle(e.sourceHandle) !== normalizeHandle(sourceHandle)) return false;
        const targetNode = nodes.find(n => n.id === e.target);
        return targetNode?.type !== 'add';
      });
      if (hasSourceEdge) return false;

      return true;
    },
    [edges, nodes],
  );

  const onConnect = useCallback(
    (params: Connection) => {
      if (!isValidConnection(params)) return;

      const normalizeHandle = (h: string | null | undefined): string | null => h ?? null;

      const existingAddEdge = edges.find(e => {
        if (e.source !== params.source || normalizeHandle(e.sourceHandle) !== normalizeHandle(params.sourceHandle))
          return false;
        const targetNode = nodes.find(n => n.id === e.target);
        return targetNode?.type === 'add';
      });

      if (existingAddEdge) {
        setEdges(es =>
          addEdge(
            { ...params, ...EDGE_CONFIG },
            es.filter(e => e.id !== existingAddEdge.id),
          ),
        );
        setNodes(ns => ns.filter(n => n.id !== existingAddEdge.target));
      } else {
        setEdges(es => addEdge({ ...params, ...EDGE_CONFIG }, es));
      }

      onChange?.();
    },
    [isValidConnection, edges, nodes, setEdges, setNodes, onChange],
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
