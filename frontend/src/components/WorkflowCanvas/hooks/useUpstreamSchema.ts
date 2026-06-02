import type { Edge, Node } from '@xyflow/react';
import { useMemo } from 'react';
import type { SchemaField } from '../types';

export const useUpstreamSchema = (
  selectedNode: Node | null,
  nodes: Node[],
  edges: Edge[],
  agentSchemas: Record<string, { output: SchemaField[] }>,
) => {
  const CEL_STEPS = ['cond', 'router', 'loop'];

  const upstreamSchema: SchemaField[] | null = useMemo(() => {
    if (!selectedNode || !CEL_STEPS.includes(selectedNode.type ?? '')) return null;
    const incomingEdge = edges.find(e => e.target === selectedNode.id);
    if (!incomingEdge) return null;
    const sourceNode = nodes.find(n => n.id === incomingEdge.source);
    if (!sourceNode) return null;
    const label = sourceNode.data?.label as string | undefined;
    return label ? (agentSchemas[label]?.output ?? null) : null;
  }, [selectedNode, edges, nodes, agentSchemas]);

  const sourceLabel: string | null = useMemo(() => {
    if (!selectedNode || !CEL_STEPS.includes(selectedNode.type ?? '')) return null;
    const incomingEdge = edges.find(e => e.target === selectedNode.id);
    if (!incomingEdge) return null;
    const sourceNode = nodes.find(n => n.id === incomingEdge.source);
    return (sourceNode?.data?.label as string | undefined) ?? null;
  }, [selectedNode, edges, nodes]);

  return { upstreamSchema, sourceLabel };
};
