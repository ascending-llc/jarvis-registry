import type { Edge, Node } from '@xyflow/react';
import type { NodeData } from '../types';

/**
 * Returns a Set of node IDs that are direct parents (upstream) of the given nodeId.
 */
export const getDirectParents = (nodeId: string, edges: Edge[]): Set<string> => {
  return new Set(edges.filter(e => e.target === nodeId).map(e => e.source));
};

/**
 * Returns a Set of "effective" executing parent node IDs for the given nodeId.
 * It traverses upstream (reverse edges) and passes through non-executing logic nodes.
 * Once an 'agent' or 'mcp' node is encountered on a path, it is added and traversal stops for that path.
 */
export const getEffectiveExecutingParents = (nodeId: string, edges: Edge[], nodes: Node<NodeData>[]): Set<string> => {
  const effectiveParents = new Set<string>();
  const visited = new Set<string>();
  const queue = [nodeId];
  
  const nodeMap = new Map(nodes.map(n => [n.id, n]));

  while (queue.length > 0) {
    const current = queue.shift()!;
    if (visited.has(current)) continue;
    visited.add(current);
    
    // Find immediate upstream physical parents
    const upstreams = edges.filter(e => e.target === current).map(e => e.source);
    
    for (const upstreamId of upstreams) {
      const upstreamNode = nodeMap.get(upstreamId);
      if (!upstreamNode) continue;
      
      if (upstreamNode.type === 'agent' || upstreamNode.type === 'mcp') {
        effectiveParents.add(upstreamId);
      } else {
        // If it's a logic node, we continue traversing upwards through it
        queue.push(upstreamId);
      }
    }
  }
  
  return effectiveParents;
};

/**
 * Returns a Set of all ancestor node IDs for the given nodeId.
 */
export const getAncestors = (nodeId: string, edges: Edge[]): Set<string> => {
  const ancestors = new Set<string>();
  const queue = [...getDirectParents(nodeId, edges)];
  
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (!ancestors.has(current)) {
      ancestors.add(current);
      queue.push(...getDirectParents(current, edges));
    }
  }
  
  return ancestors;
};

/**
 * Validates the `refs` array of all given nodes based on the current graph topology.
 * A ref is valid only if the referenced node is an ancestor of the current node.
 * 
 * Returns a new array of nodes with invalid refs removed.
 */
export const validateRefs = (nodes: Node<NodeData>[], edges: Edge[]): Node<NodeData>[] => {
  return nodes.map(node => {
    if (!node.data.refs || node.data.refs.length === 0) return node;
    
    const validAncestors = getAncestors(node.id, edges);
    const validRefs = node.data.refs.filter(refId => validAncestors.has(refId));
    
    if (validRefs.length === node.data.refs.length) {
      return node;
    }
    
    return {
      ...node,
      data: {
        ...node.data,
        refs: validRefs,
      },
    };
  });
};
