import type { Edge, Node } from '@xyflow/react';
import type { NodeData } from '../types';

const EXECUTION_NODE_TYPES = new Set(['agent', 'mcp', 'pool']);

export const isExecutionNode = (node: Node<NodeData>): boolean =>
  node.type !== undefined && EXECUTION_NODE_TYPES.has(node.type);

/** Returns the direct upstream node IDs for a node. */
export const getDirectParents = (nodeId: string, edges: Edge[]): Set<string> =>
  new Set(edges.filter(edge => edge.target === nodeId).map(edge => edge.source));

/**
 * Returns the node's direct parents that are themselves execution nodes.
 *
 * Their output is already injected as implicit input at runtime (see
 * compiler._build_implicit_previous_step_names), but only across a direct
 * edge — a structural node (Condition/Router/Loop/Parallel) between two
 * execution nodes breaks that implicit chain, so it must not be tunneled
 * through here either. A node whose direct parent is structural has no
 * effective executing parent at all.
 */
export const getEffectiveExecutingParents = (nodeId: string, edges: Edge[], nodes: Node<NodeData>[]): Set<string> => {
  const effectiveParents = new Set<string>();
  const nodeMap = new Map(nodes.map(node => [node.id, node]));

  for (const parentId of getDirectParents(nodeId, edges)) {
    const parentNode = nodeMap.get(parentId);
    if (parentNode && isExecutionNode(parentNode)) effectiveParents.add(parentId);
  }

  return effectiveParents;
};

/** Returns every upstream node ID reachable through reverse edges. */
export const getAncestors = (nodeId: string, edges: Edge[]): Set<string> => {
  const ancestors = new Set<string>();
  const queue = [...getDirectParents(nodeId, edges)];

  for (let index = 0; index < queue.length; index += 1) {
    const current = queue[index];
    if (current === undefined || ancestors.has(current)) continue;
    ancestors.add(current);
    queue.push(...getDirectParents(current, edges));
  }

  return ancestors;
};

/**
 * Returns execution nodes that may be referenced explicitly by the current step.
 * The closest execution parents are excluded because their output is already provided
 * through previous_step_content.
 */
export const getReferenceCandidates = (nodeId: string, nodes: Node<NodeData>[], edges: Edge[]): Node<NodeData>[] => {
  const ancestors = getAncestors(nodeId, edges);
  const effectiveParents = getEffectiveExecutingParents(nodeId, edges, nodes);

  return nodes.filter(
    node => node.id !== nodeId && ancestors.has(node.id) && !effectiveParents.has(node.id) && isExecutionNode(node),
  );
};

/** Removes references that are no longer valid for the latest graph topology. */
export const pruneInvalidRefs = (nodes: Node<NodeData>[], edges: Edge[]): Node<NodeData>[] =>
  nodes.map(node => {
    const refs = node.data.refs ?? [];
    if (refs.length === 0) return node;

    const validIds = new Set(getReferenceCandidates(node.id, nodes, edges).map(candidate => candidate.id));
    const nextRefs = refs.filter(refId => validIds.has(refId));
    if (nextRefs.length === refs.length) return node;

    return {
      ...node,
      data: {
        ...node.data,
        refs: nextRefs,
      },
    };
  });
