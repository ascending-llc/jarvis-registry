import type { Edge } from '@xyflow/react';
import { useCallback } from 'react';
import { getLayoutedElements } from '../layout';
import type { WorkflowNode } from '../types';

export const useCanvasLayout = (
  nodes: WorkflowNode[],
  edges: Edge[],
  setNodes: React.Dispatch<React.SetStateAction<WorkflowNode[]>>,
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>,
  onChange?: () => void,
  isReadOnly = false,
) => {
  const generateNodeId = useCallback(
    () => `n_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
    [],
  );
  const generateEdgeId = useCallback(
    () => `e_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
    [],
  );

  const runLayout = useCallback(() => {
    if (isReadOnly) return;
    const { nodes: ln, edges: le } = getLayoutedElements(nodes, edges);
    setNodes(ln);
    setEdges(le);
    onChange?.();
  }, [nodes, edges, setNodes, setEdges, onChange, isReadOnly]);

  return {
    generateNodeId,
    generateEdgeId,
    runLayout,
  };
};
