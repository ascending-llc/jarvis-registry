import dagre from '@dagrejs/dagre';
import type { Edge } from '@xyflow/react';
import { Position } from '@xyflow/react';
import { BRANCH_CANVAS_SPACING, HANDLE_SPACING, NODE_HEIGHT_DEFAULT, NODE_WIDTH } from './constants';
import type { WorkflowNode } from './types';

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

export const getLayoutedElements = (nodes: WorkflowNode[], edges: Edge[]): { nodes: WorkflowNode[]; edges: Edge[] } => {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', ranksep: 150, nodesep: 80, marginx: 40, marginy: 40 });

  nodes.forEach(node => {
    g.setNode(node.id, { width: NODE_WIDTH, height: estimateNodeHeight(node.type, node.data ?? {}) });
  });
  edges.forEach(edge => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  const layoutedNodes = nodes.map(node => {
    const pos = g.node(node.id);
    const h = estimateNodeHeight(node.type ?? '', node.data ?? {});
    return {
      ...node,
      targetPosition: Position.Left,
      sourcePosition: Position.Right,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - h / 2 },
    };
  });

  const inDeg = new Map<string, number>(layoutedNodes.map(n => [n.id, 0]));
  const adj = new Map<string, string[]>(layoutedNodes.map(n => [n.id, []]));
  for (const e of edges) {
    if (inDeg.has(e.target)) inDeg.set(e.target, (inDeg.get(e.target) ?? 0) + 1);
    if (adj.has(e.source)) {
      const list = adj.get(e.source);
      if (list) list.push(e.target);
    }
  }
  const queue = [...inDeg.entries()].filter(([, d]) => d === 0).map(([id]) => id);
  const topoOrder: string[] = [];
  while (queue.length) {
    const id = queue.shift() ?? '';
    topoOrder.push(id);
    for (const next of adj.get(id) ?? []) {
      const d = (inDeg.get(next) ?? 1) - 1;
      inDeg.set(next, d);
      if (d === 0) queue.push(next);
    }
  }

  const nodeMap = new Map(layoutedNodes.map(n => [n.id, { ...n }]));

  for (const nodeId of topoOrder) {
    const node = nodeMap.get(nodeId);
    if (!node) continue;

    const srcH = estimateNodeHeight(node.type, node.data ?? {});
    const srcCenterY = node.position.y + srcH / 2;

    let handleIds: string[] = [];
    if (node.type === 'cond') {
      handleIds = ['true', 'false'];
    } else if (node.type === 'parallel') {
      const N = ((node.data?.branches as string[]) ?? ['A', 'B']).length;
      handleIds = Array.from({ length: N }, (_, i) => `branch-${i}`);
    } else if (node.type === 'router') {
      const N = ((node.data?.cases as string[]) ?? []).length;
      handleIds = Array.from({ length: N }, (_, i) => `case-${i}`);
    }

    if (handleIds.length >= 2) {
      const N = handleIds.length;
      handleIds.forEach((handleId, i) => {
        const edge = edges.find(e => e.source === nodeId && e.sourceHandle === handleId);
        if (!edge) return;
        const target = nodeMap.get(edge.target);
        if (!target) return;
        const tgtH = estimateNodeHeight(target.type, target.data ?? {});
        const offsetY = (i - (N - 1) / 2) * BRANCH_CANVAS_SPACING;
        nodeMap.set(edge.target, {
          ...target,
          position: { ...target.position, y: srcCenterY + offsetY - tgtH / 2 },
        });
      });
    } else {
      const outEdges = edges.filter(e => e.source === nodeId);
      if (outEdges.length === 1) {
        const target = nodeMap.get(outEdges[0].target);
        if (target) {
          const tgtH = estimateNodeHeight(target.type, target.data ?? {});
          nodeMap.set(outEdges[0].target, {
            ...target,
            position: { ...target.position, y: srcCenterY - tgtH / 2 },
          });
        }
      }
    }
  }

  return { nodes: Array.from(nodeMap.values()), edges };
};
