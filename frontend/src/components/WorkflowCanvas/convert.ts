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

const mapNodeToApi = (node: Node<NodeData>, branchData: { [handle: string]: ApiWorkflowNode[] } | null): ApiWorkflowNode => {
  const data = node.data;
  const nodeType = CANVAS_TO_API_NODE_TYPE[node.type ?? ''] ?? 'step';

  const apiNode: ApiWorkflowNode = {
    id: node.id,
    name: data.label || node.id,
    nodeType,
    position: { x: Math.round(node.position?.x ?? 0), y: Math.round(node.position?.y ?? 0) },
    // Store canvasType for round-trip fidelity (mcp vs agent distinction)
    config: { description: data.description ?? '', canvasType: node.type },
  };

  if (branchData) {
    if (nodeType === 'condition') {
      apiNode.trueSteps = branchData['true'] ?? [];
      apiNode.falseSteps = branchData['false'] ?? [];
    } else if (nodeType === 'router') {
      const routerData = data as import('./types').RouterNodeData;
      const cases = routerData.cases ?? [];
      apiNode.choices = cases.map((c, i) => ({
        name: c,
        steps: branchData[`case-${i}`] ?? []
      }));
      if (branchData['default']?.length) {
         apiNode.choices.push({ name: 'default', steps: branchData['default'] });
      }
    } else {
      const keys = Object.keys(branchData).sort((a,b) => a.localeCompare(b));
      apiNode.children = keys.map(k => branchData[k][0]).filter(Boolean);
    }
  }

  if ((node.type === 'mcp' || node.type === 'agent') && data.label) {
    apiNode.executorKey = data.label;
  }
  if (node.type === 'pool')
    apiNode.a2aPool = (data as import('./types').PoolNodeData).agents?.map((a: AgentInfo) => a.id) ?? [];
  if (node.type === 'cond') apiNode.conditionCel = (data as import('./types').CondNodeData).expression ?? null;
  if (node.type === 'loop') {
    const loopData = data as import('./types').LoopNodeData;
    apiNode.loopConfig = {
      maxIterations: loopData.maxIterations ?? 10,
      endConditionCel: loopData.exitCondition,
    };
  }
  if (node.type === 'router') {
    const routerData = data as import('./types').RouterNodeData;
    apiNode.conditionCel = routerData.routeBy ?? null; // Router requires conditionCel
    apiNode.config = {
      ...apiNode.config,
      routeBy: routerData.routeBy, // Keep in config for frontend symmetry just in case
      defaultCase: routerData.defaultCase,
      cases: routerData.cases ?? [],
    };
  }
  if (node.type === 'gate') {
    const gateData = data as import('./types').GateNodeData;
    apiNode.executorKey = 'sys.approval'; // Satisfy backend Pydantic validation
    apiNode.config = {
      ...apiNode.config,
      reviewerPrompt: gateData.reviewerPrompt,
      role: gateData.role,
      timeout: gateData.timeout,
      onTimeout: gateData.onTimeout,
    };
  }

  return apiNode;
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
      const branchData: { [handle: string]: ApiWorkflowNode[] } = {};
      
      for (const e of sortedEdges) {
        const handle = e.sourceHandle || 'default';
        branchData[handle] = buildSequence(e.target, nodeMap, edgesFromNode, addNodeIds, new Set(visited));
      }

      result.push(mapNodeToApi(node, branchData));
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
    const trueSteps = node.trueSteps ?? [];
    const falseSteps = node.falseSteps ?? [];
    const choices = node.choices ?? [];

    if (node.nodeType === 'step') {
      if (children.length > 0 || trueSteps.length > 0 || falseSteps.length > 0 || choices.length > 0) 
        return `Step node "${node.name}" must not have nested branches`;
      if (node.config?.canvasType !== 'gate' && !node.executorKey && (!node.a2aPool || node.a2aPool.length === 0)) {
        return `Node "${node.name}" requires an executor key or agent pool`;
      }
    }

    if (node.nodeType === 'condition') {
      if (trueSteps.length === 0) {
        return `Condition node "${node.name}" requires at least one node in the true branch`;
      }
      for (const child of [...trueSteps, ...falseSteps]) {
        const err = visit(child);
        if (err) return err;
      }
      return null;
    }

    if (node.nodeType === 'router') {
      if (choices.length < 2) {
        return `Router node "${node.name}" requires at least 2 choices`;
      }
      for (const choice of choices) {
        if (choice.steps.length === 0) return `Router choice "${choice.name}" in node "${node.name}" requires at least one node`;
        for (const child of choice.steps) {
          const err = visit(child);
          if (err) return err;
        }
      }
      return null;
    }

    if (node.nodeType === 'parallel' && children.length < 2) {
      return `Parallel node "${node.name}" requires at least 2 branches`;
    }

    if (node.nodeType === 'loop' && children.length < 1) {
      return `Loop node "${node.name}" requires at least one branch`;
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

  const data = {
    label: w.name,
    description: (w.config?.description as string | undefined) ?? '',
  } as NodeData;

  if (canvasType === 'cond' && w.conditionCel) {
    (data as import('./types').CondNodeData).expression = w.conditionCel;
  }
  if (canvasType === 'router' && w.conditionCel) {
    (data as import('./types').RouterNodeData).routeBy = w.conditionCel;
  } else if (w.config?.routeBy) {
    (data as import('./types').RouterNodeData).routeBy = w.config.routeBy as string;
  }
  if (w.loopConfig) {
    const loopData = data as import('./types').LoopNodeData;
    loopData.maxIterations = w.loopConfig.maxIterations;
    loopData.exitCondition = w.loopConfig.endConditionCel;
  }
  if (w.config?.routeBy) (data as import('./types').RouterNodeData).routeBy = w.config.routeBy as string;
  if (w.config?.defaultCase) (data as import('./types').RouterNodeData).defaultCase = w.config.defaultCase as string;
  if (w.config?.cases) (data as import('./types').RouterNodeData).cases = w.config.cases as string[];
  if (w.a2aPool)
    (data as import('./types').PoolNodeData).agents = w.a2aPool.map(
      id => ({ id, label: id, desc: '' }) satisfies AgentInfo,
    );

  // Restore branch/case labels for parallel & router handles
  if (canvasType === 'parallel') {
    (data as import('./types').ParallelNodeData).branches = (w.children ?? []).map(
      (c, i) => c.name || `Branch ${String.fromCharCode(65 + i)}`,
    );
  }
  if (canvasType === 'router') {
    (data as import('./types').RouterNodeData).cases =
      (w.config?.cases as string[] | undefined) ?? (w.children ?? []).map((_, i) => `case-${i}`);
  }

  return {
    id: w.id ?? `n_${Date.now()}_${Math.random()}`,
    type: canvasType,
    position: w.position ? { x: w.position.x ?? 0, y: w.position.y ?? 0 } : { x: 0, y: 0 },
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

    if (BRANCHING_TYPES.has(canvasNode.type ?? '')) {
      const type = canvasNode.type;
      
      let branches: { handle: string; sequence: ApiWorkflowNode[] }[] = [];
      
      if (type === 'cond') {
        const tSteps = apiNode.trueSteps?.length ? apiNode.trueSteps : (apiNode.children?.[0] ? [apiNode.children[0]] : []);
        const fSteps = apiNode.falseSteps?.length ? apiNode.falseSteps : (apiNode.children?.[1] ? [apiNode.children[1]] : []);
        if (tSteps.length > 0) branches.push({ handle: 'true', sequence: tSteps });
        if (fSteps.length > 0) branches.push({ handle: 'false', sequence: fSteps });
      } else if (type === 'router') {
        let cases = apiNode.choices ?? [];
        if (cases.length === 0 && apiNode.children?.length) {
           // fallback to children
           const routerCases = (apiNode.config?.cases as string[]) ?? [];
           cases = apiNode.children.map((c, i) => {
             const name = routerCases[i] ?? (i === apiNode.children!.length - 1 ? 'default' : `case-${i}`);
             return { name, steps: [c] };
           });
        }
        const routerCases = (apiNode.config?.cases as string[]) ?? [];
        cases.forEach(choice => {
          let handle = 'default';
          const idx = routerCases.indexOf(choice.name);
          if (idx >= 0) handle = `case-${idx}`;
          if (choice.name === 'default') handle = 'default';
          
          if (choice.steps.length > 0) {
            branches.push({ handle, sequence: choice.steps });
          }
        });
      } else if (type === 'parallel') {
        const children = apiNode.children ?? [];
        children.forEach((child, i) => {
          branches.push({ handle: `branch-${i}`, sequence: [child] });
        });
      } else if (type === 'loop') {
        const children = apiNode.children ?? [];
        if (children.length > 0) {
          branches.push({ handle: 'loop', sequence: children });
        }
      }

      for (const branch of branches) {
        const branchLastId = processApiSequence(branch.sequence, canvasNode.id, branch.handle, result);
        
        const finalBranchNodeId = branchLastId ?? canvasNode.id;
        const finalNode = result.nodes.find(n => n.id === finalBranchNodeId) || canvasNode;
        
        const addId = nextAddId();
        result.nodes.push({ 
          id: addId, 
          type: 'add', 
          position: { x: (finalNode.position?.x ?? 0) + 300, y: finalNode.position?.y ?? 0 }, 
          data: { label: '' } 
        });
        result.edges.push({ id: nextEdgeId(), source: finalNode.id, target: addId, ...DASHED_EDGE });
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
    const lastNode = result.nodes.find(n => n.id === lastStepId);
    const addId = nextAddId();
    result.nodes.push({ 
      id: addId, 
      type: 'add', 
      position: { x: (lastNode?.position.x ?? 0) + 300, y: lastNode?.position.y ?? 0 }, 
      data: { label: '' } 
    });
    result.edges.push({ id: nextEdgeId(), source: lastStepId, target: addId, ...DASHED_EDGE });
  }

  // If any node has a saved position (e.g. from backend), we should not overwrite with Dagre auto-layout.
  // Note: we check if the first non-add node has a position set to something other than 0,0.
  // A more robust check is to just see if the backend provided any `position` object at all on the first node.
  const hasSavedPositions = apiNodes.length > 0 && !!apiNodes[0].position;

  return hasSavedPositions ? result : getLayoutedElements(result.nodes, result.edges);
};
