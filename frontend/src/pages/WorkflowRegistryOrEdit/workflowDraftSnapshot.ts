import type { Edge } from '@xyflow/react';
import type { WorkflowNode } from '@/components/WorkflowCanvas/types';

type JsonPrimitive = boolean | number | string | null;
type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

interface WorkflowDraftNode {
  id: string;
  type: string | null;
  position: { x: number; y: number };
  data: JsonValue;
}

interface WorkflowDraftEdge {
  source: string;
  target: string;
  sourceHandle: string | null;
  targetHandle: string | null;
}

interface WorkflowDraftSnapshot {
  name: string;
  description: string;
  nodes: WorkflowDraftNode[];
  edges: WorkflowDraftEdge[];
}

interface WorkflowDraftMetadata {
  name?: string;
  description?: string;
}

const _isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const _normalizeValue = (value: unknown, ancestors: WeakSet<object>): JsonValue | undefined => {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'undefined' || typeof value === 'function' || typeof value === 'symbol') return undefined;

  if (Array.isArray(value)) {
    if (ancestors.has(value)) return undefined;
    ancestors.add(value);
    const normalized = value
      .map(item => _normalizeValue(item, ancestors))
      .filter((item): item is JsonValue => item !== undefined);
    ancestors.delete(value);
    return normalized;
  }

  if (!_isRecord(value)) return undefined;
  if (ancestors.has(value)) return undefined;

  ancestors.add(value);
  const normalized: { [key: string]: JsonValue } = {};
  for (const key of Object.keys(value).sort()) {
    const item = _normalizeValue(value[key], ancestors);
    if (item !== undefined) normalized[key] = item;
  }
  ancestors.delete(value);
  return normalized;
};

const _normalizeNodeData = (data: unknown): JsonValue => _normalizeValue(data, new WeakSet<object>()) ?? null;

const _roundPosition = (value: number | undefined): number => (Number.isFinite(value) ? Math.round(value ?? 0) : 0);

const _compareNodes = (left: WorkflowDraftNode, right: WorkflowDraftNode): number => left.id.localeCompare(right.id);

const _compareEdges = (left: WorkflowDraftEdge, right: WorkflowDraftEdge): number => {
  const leftKey = [left.source, left.target, left.sourceHandle ?? '', left.targetHandle ?? ''].join('\u0000');
  const rightKey = [right.source, right.target, right.sourceHandle ?? '', right.targetHandle ?? ''].join('\u0000');
  return leftKey.localeCompare(rightKey);
};

/**
 * Produces a stable representation of persisted workflow content only.
 * React Flow presentation state and add-node placeholders are deliberately omitted.
 */
export const createWorkflowDraftSnapshot = (
  workflow: WorkflowDraftMetadata,
  nodes: WorkflowNode[],
  edges: Edge[],
): string => {
  const businessNodes = nodes.filter(node => node.type !== 'add');
  const businessNodeIds = new Set(businessNodes.map(node => node.id));

  const snapshot: WorkflowDraftSnapshot = {
    name: workflow.name ?? '',
    description: workflow.description ?? '',
    nodes: businessNodes
      .map(node => ({
        id: node.id,
        type: node.type ?? null,
        position: {
          x: _roundPosition(node.position.x),
          y: _roundPosition(node.position.y),
        },
        data: _normalizeNodeData(node.data),
      }))
      .sort(_compareNodes),
    edges: edges
      .filter(edge => businessNodeIds.has(edge.source) && businessNodeIds.has(edge.target))
      .map(edge => ({
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.sourceHandle ?? null,
        targetHandle: edge.targetHandle ?? null,
      }))
      .sort(_compareEdges),
  };

  return JSON.stringify(snapshot);
};

export type { WorkflowDraftMetadata };
