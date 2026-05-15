import type { Edge, Node } from '@xyflow/react';
import type { WorkflowNode as ApiWorkflowNode } from '@/services/workflow/type';
import { DASHED_EDGE, EDGE_CONFIG } from './constants';
import { getLayoutedElements } from './layout';
import type { AgentInfo, WorkflowNode as CanvasNode, NodeData } from './types';

// ─── canvas type ↔ API nodeType maps ─────────────────────────────────────────

const BRANCHING_TYPES = new Set(['cond', 'parallel', 'loop', 'router']);

const CANVAS_TO_API_NODE_TYPE: Record<string, ApiWorkflowNode['nodeType']> = {
  mcp: 'step',
  agent: 'step',
  pool: 'step',
  cond: 'condition',
  parallel: 'parallel',
  loop: 'loop',
  router: 'router',
};

const API_TO_CANVAS_TYPE: Record<string, string> = {
  condition: 'cond',
  parallel: 'parallel',
  loop: 'loop',
  router: 'router',
};

// ─── canvas → API ─────────────────────────────────────────────────────────────

const sortEdgesByHandle = (edges: Edge[]): Edge[] =>
  [...edges].sort((a, b) => {
    const ha = a.sourceHandle ?? '';
    const hb = b.sourceHandle ?? '';
    if (ha === 'true' && hb === 'false') return -1;
    if (ha === 'false' && hb === 'true') return 1;
    const ia = Number.parseInt(ha.replace(/\D/g, '') || '0', 10);
    const ib = Number.parseInt(hb.replace(/\D/g, '') || '0', 10);
    return ia - ib;
  });

const mapNodeToApi = (node: Node<NodeData>, children: ApiWorkflowNode[] | null): ApiWorkflowNode => {
  const data = node.data;
  const nodeType = CANVAS_TO_API_NODE_TYPE[node.type ?? ''] ?? 'step';

  const apiNode: ApiWorkflowNode = {
    id: node.id,
    name: data.label || node.id,
    nodeType,
    // Store canvasType for round-trip fidelity (mcp vs agent distinction)
    config: { description: data.description ?? '', canvasType: node.type },
  };

  if (children !== null) apiNode.children = children;

  // Only include executorKey when non-empty — backend rejects null with no a2aPool
  if ((node.type === 'mcp' || node.type === 'agent') && data.label) {
    apiNode.executorKey = data.label;
  }
  if (node.type === 'pool') apiNode.a2aPool = (data.agents ?? []).map((a: AgentInfo) => a.id);
  if (node.type === 'cond') apiNode.conditionCel = data.expression ?? null;
  if (node.type === 'loop') {
    apiNode.loopConfig = {
      maxIterations: data.maxIterations ?? 10,
      endConditionCel: data.exitCondition,
    };
  }
  if (node.type === 'router') {
    apiNode.config = {
      ...apiNode.config,
      routeBy: data.routeBy,
      defaultCase: data.defaultCase,
      cases: data.cases ?? [],
    };
  }

  return apiNode;
};

const buildBranchHead = (
  nodeId: string,
  nodeMap: Map<string, Node<NodeData>>,
  edgesFromNode: Map<string, Edge[]>,
  addNodeIds: Set<string>,
): ApiWorkflowNode | null => {
  const node = nodeMap.get(nodeId);
  if (!node || addNodeIds.has(nodeId)) return null;

  if (BRANCHING_TYPES.has(node.type ?? '')) {
    const outEdges = sortEdgesByHandle((edgesFromNode.get(nodeId) ?? []).filter(e => !addNodeIds.has(e.target)));
    const children = outEdges
      .map(e => buildBranchHead(e.target, nodeMap, edgesFromNode, addNodeIds))
      .filter((n): n is ApiWorkflowNode => n !== null);
    return mapNodeToApi(node, children);
  }

  return mapNodeToApi(node, null);
};

const buildSequence = (
  startId: string,
  nodeMap: Map<string, Node<NodeData>>,
  edgesFromNode: Map<string, Edge[]>,
  addNodeIds: Set<string>,
  visited: Set<string>,
): ApiWorkflowNode[] => {
  const result: ApiWorkflowNode[] = [];
  let currentId: string | null = startId;

  while (currentId !== null) {
    if (visited.has(currentId) || addNodeIds.has(currentId)) break;
    const node = nodeMap.get(currentId);
    if (!node) break;

    visited.add(currentId);

    const rawEdges: Edge[] = edgesFromNode.get(currentId) ?? [];
    const outEdges: Edge[] = rawEdges.filter((e: Edge) => !addNodeIds.has(e.target));

    if (BRANCHING_TYPES.has(node.type ?? '')) {
      const sortedEdges = sortEdgesByHandle(outEdges);
      const children = sortedEdges
        .map(e => buildBranchHead(e.target, nodeMap, edgesFromNode, addNodeIds))
        .filter((n): n is ApiWorkflowNode => n !== null);

      result.push(mapNodeToApi(node, children));
      currentId = null;
    } else {
      result.push(mapNodeToApi(node, null));
      const next: Edge | undefined = outEdges.find((e: Edge) => nodeMap.has(e.target));
      currentId = next?.target ?? null;
    }
  }

  return result;
};

/** Walk API tree and return first validation error message, or null if valid. */
export const validateApiNodes = (apiNodes: ApiWorkflowNode[]): string | null => {
  const visit = (node: ApiWorkflowNode): string | null => {
    const children = node.children ?? [];

    if (node.nodeType === 'step') {
      if (children.length > 0) return `Step node "${node.name}" must not have children`;
      if (!node.executorKey && (!node.a2aPool || node.a2aPool.length === 0)) {
        return `Node "${node.name}" requires an executor key or agent pool`;
      }
    }

    if (node.nodeType === 'condition') {
      if (children.length === 0) {
        return `Condition node "${node.name}" requires at least one branch with a step node`;
      }
      if (children.length > 2) {
        return `Condition node "${node.name}" supports at most 2 branches`;
      }
    }

    if (node.nodeType === 'parallel' && children.length < 2) {
      return `Parallel node "${node.name}" requires at least 2 branches with step nodes`;
    }

    if (node.nodeType === 'loop' && children.length < 1) {
      return `Loop node "${node.name}" requires at least one branch with a step node`;
    }

    if (node.nodeType === 'router' && children.length < 2) {
      return `Router node "${node.name}" requires at least 2 branches with step nodes`;
    }

    for (const child of children) {
      const err = visit(child);
      if (err) return err;
    }
    return null;
  };

  for (const root of apiNodes) {
    const err = visit(root);
    if (err) return err;
  }
  return null;
};

/**
 * Convert ReactFlow nodes + edges → API `WorkflowNode[]` payload.
 * "add" placeholder nodes are excluded; branching nodes carry their branches in `children`.
 */
export const canvasToApiNodes = (nodes: Node<NodeData>[], edges: Edge[]): ApiWorkflowNode[] => {
  const addNodeIds = new Set(nodes.filter(n => n.type === 'add').map(n => n.id));
  const workNodes = nodes.filter(n => n.type !== 'add');
  if (workNodes.length === 0) return [];

  const nodeMap = new Map(workNodes.map(n => [n.id, n]));
  const workEdges = edges.filter(e => !addNodeIds.has(e.source) && !addNodeIds.has(e.target));

  const edgesFromNode = new Map<string, Edge[]>(workNodes.map(n => [n.id, []]));
  for (const e of workEdges) edgesFromNode.get(e.source)?.push(e);

  const targetIds = new Set(workEdges.map(e => e.target));
  const roots = workNodes.filter(n => !targetIds.has(n.id));

  const visited = new Set<string>();
  return roots.flatMap(n => buildSequence(n.id, nodeMap, edgesFromNode, addNodeIds, visited));
};

// ─── API → canvas ─────────────────────────────────────────────────────────────

interface CanvasResult {
  nodes: CanvasNode[];
  edges: Edge[];
}

let _nodeSeq = 0;
let _edgeSeq = 0;
const nextAddId = () => `add${_nodeSeq++}`;
const nextEdgeId = () => `e_load_${_edgeSeq++}`;

const apiNodeToCanvas = (w: ApiWorkflowNode): CanvasNode => {
  // Restore original canvas type if stored; fall back to API-type mapping
  const canvasType: string = (w.config?.canvasType as string | undefined) ?? API_TO_CANVAS_TYPE[w.nodeType] ?? 'agent';

  const data: NodeData = {
    label: w.name,
    description: (w.config?.description as string | undefined) ?? '',
  };

  if (w.conditionCel) data.expression = w.conditionCel;
  if (w.loopConfig) {
    data.maxIterations = w.loopConfig.maxIterations;
    data.exitCondition = w.loopConfig.endConditionCel;
  }
  if (w.config?.routeBy) data.routeBy = w.config.routeBy as string;
  if (w.config?.defaultCase) data.defaultCase = w.config.defaultCase as string;
  if (w.config?.cases) data.cases = w.config.cases as string[];
  if (w.a2aPool) data.agents = w.a2aPool.map(id => ({ id, label: id, desc: '' }) satisfies AgentInfo);

  // Restore branch/case labels for parallel & router handles
  if (canvasType === 'parallel') {
    data.branches = (w.children ?? []).map((c, i) => c.name || `Branch ${String.fromCharCode(65 + i)}`);
  }
  if (canvasType === 'router') {
    data.cases = (w.config?.cases as string[] | undefined) ?? (w.children ?? []).map((_, i) => `case-${i}`);
  }

  return {
    id: w.id ?? `n_${Date.now()}_${Math.random()}`,
    type: canvasType,
    position: { x: 0, y: 0 },
    data,
  };
};

/**
 * Process a flat API sequence, appending canvas nodes + edges to `result`.
 * Returns the ID of the last step node (null if the sequence ended at a branching node).
 */
const processApiSequence = (
  apiNodes: ApiWorkflowNode[],
  prevId: string | null,
  prevHandle: string | null,
  result: CanvasResult,
): string | null => {
  let lastStepId: string | null = prevId;
  let lastHandle: string | null = prevHandle;

  for (const apiNode of apiNodes) {
    const canvasNode = apiNodeToCanvas(apiNode);
    result.nodes.push(canvasNode);

    if (lastStepId !== null) {
      result.edges.push({
        id: nextEdgeId(),
        source: lastStepId,
        target: canvasNode.id,
        ...(lastHandle ? { sourceHandle: lastHandle } : {}),
        ...EDGE_CONFIG,
      });
    }

    const children = apiNode.children ?? [];

    if (BRANCHING_TYPES.has(canvasNode.type ?? '')) {
      // Determine source handles for each branch
      const handles: string[] =
        canvasNode.type === 'cond'
          ? ['true', 'false']
          : canvasNode.type === 'parallel'
            ? (canvasNode.data.branches ?? []).map((_: string, i: number) => `branch-${i}`)
            : canvasNode.type === 'router'
              ? (canvasNode.data.cases ?? []).map((_: string, i: number) => `case-${i}`)
              : children.map((_, i) => `branch-${i}`);

      for (let i = 0; i < children.length; i++) {
        const branchChild = apiNodeToCanvas(children[i]);
        result.nodes.push(branchChild);
        result.edges.push({
          id: nextEdgeId(),
          source: canvasNode.id,
          target: branchChild.id,
          sourceHandle: handles[i] ?? `branch-${i}`,
          ...EDGE_CONFIG,
        });
        // Terminate each branch with an 'add' placeholder
        const addId = nextAddId();
        result.nodes.push({ id: addId, type: 'add', position: { x: 0, y: 0 }, data: { label: '' } });
        result.edges.push({ id: nextEdgeId(), source: branchChild.id, target: addId, ...DASHED_EDGE });
      }

      lastStepId = null;
      lastHandle = null;
    } else {
      lastStepId = canvasNode.id;
      lastHandle = null;
    }
  }

  return lastStepId;
};

/**
 * Convert API `WorkflowNode[]` → ReactFlow nodes + edges, ready for the canvas.
 * Positions are computed via `getLayoutedElements`.
 */
export const apiNodesToCanvas = (apiNodes: ApiWorkflowNode[]): { nodes: CanvasNode[]; edges: Edge[] } => {
  _nodeSeq = 0;
  _edgeSeq = 0;

  if (apiNodes.length === 0) {
    return {
      nodes: [{ id: 'add0', type: 'add', position: { x: 0, y: 0 }, data: { label: '' } }],
      edges: [],
    };
  }

  const result: CanvasResult = { nodes: [], edges: [] };
  const lastStepId = processApiSequence(apiNodes, null, null, result);

  // Trailing 'add' node after the last step in the linear chain
  if (lastStepId !== null) {
    const addId = nextAddId();
    result.nodes.push({ id: addId, type: 'add', position: { x: 0, y: 0 }, data: { label: '' } });
    result.edges.push({ id: nextEdgeId(), source: lastStepId, target: addId, ...DASHED_EDGE });
  }

  return getLayoutedElements(result.nodes, result.edges);
};
