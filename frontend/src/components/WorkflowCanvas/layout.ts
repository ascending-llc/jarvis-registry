import type { Edge } from '@xyflow/react';
import { Position } from '@xyflow/react';
import { HANDLE_SPACING, NODE_HEIGHT_DEFAULT, NODE_WIDTH } from './constants';
import type { WorkflowNode } from './types';

// Layout Constants
const RANK_SEP_X = 150; // Horizontal spacing between nodes
const NODE_SEP_Y = 40; // Minimum vertical spacing between sibling subtrees

export const estimateNodeHeight = (type: string | undefined, data: Record<string, unknown>): number => {
  const t = type ?? '';
  if (t === 'add') return 72;
  if (t === 'parallel') {
    const branches = data.branches as string[] | undefined;
    const N = Array.isArray(branches) ? branches.length : 2;
    return Math.max(80, (N - 1) * HANDLE_SPACING + 60) + 50;
  }
  if (t === 'router') {
    const cases = data.cases as string[] | undefined;
    const N = Array.isArray(cases) ? cases.length : 2;
    return Math.max(80, (N - 1) * HANDLE_SPACING + 60) + 50;
  }
  if (t === 'cond') return HANDLE_SPACING + 60 + 50;
  return NODE_HEIGHT_DEFAULT;
};

interface LayoutTree {
  nodeId: string;
  width: number;
  height: number;
  subtreeHeight: number;
  children: {
    handleId: string | null;
    targetId: string;
    targetTree: LayoutTree | null;
  }[];
}

export const getLayoutedElements = (nodes: WorkflowNode[], edges: Edge[]): { nodes: WorkflowNode[]; edges: Edge[] } => {
  if (nodes.length === 0) return { nodes, edges };

  const nodeMap = new Map<string, WorkflowNode>(nodes.map(n => [n.id, n]));
  const inDeg = new Map<string, number>(nodes.map(n => [n.id, 0]));
  const outEdges = new Map<string, Edge[]>(nodes.map(n => [n.id, []]));

  for (const e of edges) {
    if (inDeg.has(e.target)) inDeg.set(e.target, (inDeg.get(e.target) ?? 0) + 1);
    if (outEdges.has(e.source)) {
      outEdges.get(e.source)!.push(e);
    }
  }

  // Find root nodes (in-degree 0)
  const roots = nodes.filter(n => (inDeg.get(n.id) ?? 0) === 0);

  const globalVisited = new Set<string>();

  const buildTree = (nodeId: string): LayoutTree | null => {
    if (globalVisited.has(nodeId)) return null;
    globalVisited.add(nodeId);

    const node = nodeMap.get(nodeId);
    if (!node) return null;

    const fallbackH = estimateNodeHeight(node.type, node.data ?? {});
    const h = node.measured?.height ?? node.height ?? fallbackH;
    const w = node.measured?.width ?? node.width ?? NODE_WIDTH;

    const tree: LayoutTree = {
      nodeId,
      width: w,
      height: h,
      subtreeHeight: h,
      children: [],
    };

    // Determine the expected order of handles for this node type
    let handleIds: string[] = [];
    if (node.type === 'cond') {
      handleIds = ['true', 'false'];
    } else if (node.type === 'parallel') {
      const N = ((node.data?.branches as string[]) ?? ['A', 'B']).length;
      handleIds = Array.from({ length: N }, (_, i) => `branch-${i}`);
    } else if (node.type === 'router') {
      const N = ((node.data?.cases as string[]) ?? []).length;
      handleIds = Array.from({ length: N }, (_, i) => `case-${i}`);
      if (node.data?.defaultCase) handleIds.push('default');
    } else if (node.type === 'loop') {
      handleIds = ['body', 'exit'];
    }

    const edgesFromNode = outEdges.get(nodeId) || [];

    // Sort edges sequentially by handle order to prevent cross-overs
    const sortedEdges = edgesFromNode.slice().sort((a, b) => {
      const idxA = a.sourceHandle ? handleIds.indexOf(a.sourceHandle) : -1;
      const idxB = b.sourceHandle ? handleIds.indexOf(b.sourceHandle) : -1;
      if (idxA !== -1 && idxB !== -1) return idxA - idxB;
      if (idxA !== -1) return -1;
      if (idxB !== -1) return 1;
      return 0;
    });

    // Compute subtree bounds (Bottom-Up)
    let totalChildrenHeight = 0;
    for (const edge of sortedEdges) {
      const childTree = buildTree(edge.target);
      tree.children.push({
        handleId: edge.sourceHandle ?? null,
        targetId: edge.target,
        targetTree: childTree,
      });
      if (childTree) {
        totalChildrenHeight += childTree.subtreeHeight;
      }
    }

    const activeChildrenCount = tree.children.filter(c => c.targetTree).length;
    if (activeChildrenCount > 0) {
      const padding = (activeChildrenCount - 1) * NODE_SEP_Y;
      tree.subtreeHeight = Math.max(h, totalChildrenHeight + padding);
    }

    return tree;
  };

  const layoutedNodes = new Map<string, WorkflowNode>();

  const positionTree = (tree: LayoutTree, startX: number, centerY: number) => {
    const node = nodeMap.get(tree.nodeId);
    if (node) {
      layoutedNodes.set(tree.nodeId, {
        ...node,
        targetPosition: Position.Left,
        sourcePosition: Position.Right,
        position: { x: startX, y: centerY - tree.height / 2 },
      });
    }

    const childrenWithTrees = tree.children.filter(c => c.targetTree);
    if (childrenWithTrees.length === 0) return;

    // Distribute subtrees vertically relative to the parent's center
    const totalH =
      childrenWithTrees.reduce((sum, c) => sum + c.targetTree!.subtreeHeight, 0) +
      (childrenWithTrees.length - 1) * NODE_SEP_Y;
    let currentY = centerY - totalH / 2;

    for (const child of tree.children) {
      if (child.targetTree) {
        const childCenterY = currentY + child.targetTree.subtreeHeight / 2;
        positionTree(child.targetTree, startX + tree.width + RANK_SEP_X, childCenterY);
        currentY += child.targetTree.subtreeHeight + NODE_SEP_Y;
      }
    }
  };

  let currentRootY = 0;

  // Layout from valid roots
  for (const root of roots) {
    if (globalVisited.has(root.id)) continue;
    const tree = buildTree(root.id);
    if (tree) {
      positionTree(tree, 50, currentRootY + tree.subtreeHeight / 2);
      currentRootY += tree.subtreeHeight + NODE_SEP_Y;
    }
  }

  // Fallback for floating disconnected cycles
  for (const node of nodes) {
    if (!globalVisited.has(node.id)) {
      const tree = buildTree(node.id);
      if (tree) {
        positionTree(tree, 50, currentRootY + tree.subtreeHeight / 2);
        currentRootY += tree.subtreeHeight + NODE_SEP_Y;
      }
    }
  }

  // Preserve any unvisited nodes (though globalVisited should catch all)
  const finalNodes = nodes.map(n => layoutedNodes.get(n.id) || n);

  return { nodes: finalNodes, edges };
};
