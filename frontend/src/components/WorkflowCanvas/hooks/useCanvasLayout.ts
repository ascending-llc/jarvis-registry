import type { Edge, Node } from '@xyflow/react';
import { useCallback, useRef } from 'react';
import { getLayoutedElements } from '../layout';
import type { WorkflowNode } from '../types';

export const useCanvasLayout = (
  nodes: WorkflowNode[],
  edges: Edge[],
  setNodes: React.Dispatch<React.SetStateAction<WorkflowNode[]>>,
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>,
  onChange?: () => void,
) => {
  const nodeIdRef = useRef(0);
  const edgeIdRef = useRef(0);

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

  const generateNodeId = useCallback(() => `n${++nodeIdRef.current}`, []);
  const generateEdgeId = useCallback(() => `e${++edgeIdRef.current}`, []);

  const runLayout = useCallback(() => {
    const { nodes: ln, edges: le } = getLayoutedElements(nodes, edges);
    setNodes(ln);
    setEdges(le);
    onChange?.();
  }, [nodes, edges, setNodes, setEdges, onChange]);

  return {
    syncIdCounters,
    generateNodeId,
    generateEdgeId,
    runLayout,
  };
};
