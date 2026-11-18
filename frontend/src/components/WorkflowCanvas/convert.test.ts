import type { Node } from '@xyflow/react';
import { describe, expect, it } from 'vitest';
import type { WorkflowNode as ApiWorkflowNode } from '@/services/workflow/type';
import { apiNodesToCanvas, canvasToApiNodes, validateCanvasNodes } from './convert';
import type { NodeData } from './types';

const POSITION = { x: 0, y: 0 };

const step = (
  id: string,
  canvasType: 'mcp' | 'pool' | 'agent',
  modelSourceId: string | null | undefined,
  extra: Partial<ApiWorkflowNode> = {},
): ApiWorkflowNode => ({
  id,
  name: id,
  nodeType: 'step',
  stepObjective: `Objective for ${id}`,
  ...(canvasType === 'pool' ? { a2aPool: ['/agents/a'] } : { executorKey: `${id}-executor` }),
  ...(modelSourceId !== undefined ? { modelSourceId } : {}),
  config: { description: '', canvasType },
  position: POSITION,
  ...extra,
});

/** Flattens the API tree so nested steps can be looked up by id. */
const flatten = (nodes: ApiWorkflowNode[]): ApiWorkflowNode[] =>
  nodes.flatMap(node => [
    node,
    ...flatten(node.children ?? []),
    ...flatten(node.trueSteps ?? []),
    ...flatten(node.falseSteps ?? []),
    ...flatten((node.choices ?? []).flatMap(choice => choice.steps)),
  ]);

const roundTrip = (apiNodes: ApiWorkflowNode[]): ApiWorkflowNode[] => {
  const { nodes, edges } = apiNodesToCanvas(apiNodes);
  return flatten(canvasToApiNodes(nodes, edges) as ApiWorkflowNode[]);
};

const byId = (nodes: ApiWorkflowNode[], id: string): ApiWorkflowNode => {
  const node = nodes.find(candidate => candidate.id === id);
  if (!node) throw new Error(`node ${id} not found`);
  return node;
};

describe('modelSourceId round-trip', () => {
  it('preserves modelSourceId on mcp, pool and agent step nodes', () => {
    const saved = roundTrip([
      step('mcp1', 'mcp', 'model-x'),
      step('pool1', 'pool', 'model-y'),
      step('agent1', 'agent', 'model-z'),
    ]);

    expect(byId(saved, 'mcp1').modelSourceId).toBe('model-x');
    expect(byId(saved, 'pool1').modelSourceId).toBe('model-y');
    expect(byId(saved, 'agent1').modelSourceId).toBe('model-z');
  });

  it('keeps modelSourceId on a step behind an Approval Gate across gate split and merge', () => {
    const humanReview = {
      requiresConfirmation: true,
      confirmationMessage: 'Proceed?',
      timeoutSeconds: 4 * 60 * 60,
      onTimeout: 'cancel' as const,
    };
    const { nodes, edges } = apiNodesToCanvas([
      step('first', 'mcp', null),
      step('gated', 'mcp', 'model-x', { humanReview }),
    ]);

    const gate = nodes.find(node => node.type === 'gate');
    expect(gate).toBeDefined();
    expect(gate?.data.modelSourceId).toBeUndefined();

    const saved = flatten(canvasToApiNodes(nodes, edges) as ApiWorkflowNode[]);
    expect(saved.map(node => node.id)).toEqual(['first', 'gated']);
    expect(byId(saved, 'gated').modelSourceId).toBe('model-x');
    expect(byId(saved, 'gated').humanReview).toMatchObject({ confirmationMessage: 'Proceed?' });
    expect(byId(saved, 'first').modelSourceId).toBeNull();
  });

  it('keeps modelSourceId on steps nested inside a condition branch', () => {
    const condition: ApiWorkflowNode = {
      id: 'cond1',
      name: 'cond1',
      nodeType: 'condition',
      conditionCel: 'true',
      trueSteps: [step('nestedTrue', 'mcp', 'model-x')],
      falseSteps: [step('nestedFalse', 'pool', 'model-y')],
      config: { description: '', canvasType: 'cond' },
      position: POSITION,
    };

    const saved = roundTrip([condition]);

    expect(byId(saved, 'nestedTrue').modelSourceId).toBe('model-x');
    expect(byId(saved, 'nestedFalse').modelSourceId).toBe('model-y');
    expect('modelSourceId' in byId(saved, 'cond1')).toBe(false);
  });

  it('emits null for step nodes without an override and omits the field on non-step nodes', () => {
    const loop: ApiWorkflowNode = {
      id: 'loop1',
      name: 'loop1',
      nodeType: 'loop',
      loopConfig: { maxIterations: 3 },
      children: [step('body', 'agent', undefined)],
      config: { description: '', canvasType: 'loop' },
      position: POSITION,
    };

    const saved = roundTrip([step('noOverride', 'mcp', undefined), loop]);

    expect(byId(saved, 'noOverride').modelSourceId).toBeNull();
    expect(byId(saved, 'body').modelSourceId).toBeNull();
    expect('modelSourceId' in byId(saved, 'loop1')).toBe(false);
  });
});

describe('validateCanvasNodes model source check', () => {
  const canvasNode = (id: string, type: string, modelSourceId: string | null): Node<NodeData> => ({
    id,
    type,
    position: POSITION,
    data: { label: id, stepObjective: 'Do it', modelSourceId },
  });

  const deletedMessage = (name: string) =>
    `Step node "${name}" uses a model source that no longer exists. Pick another model or Workspace default.`;

  const known = new Set(['model-x']);

  it.each(['mcp', 'pool'])('rejects a %s node whose model source is not a known chat model', type => {
    expect(validateCanvasNodes([canvasNode('Step', type, 'gone')], [], known)).toBe(deletedMessage('Step'));
  });

  it('accepts known model sources and null overrides', () => {
    const nodes = [canvasNode('A', 'mcp', 'model-x'), canvasNode('B', 'pool', null)];
    expect(validateCanvasNodes(nodes, [], known)).toBeNull();
  });

  it('skips agent nodes, whose field is hidden in the UI', () => {
    expect(validateCanvasNodes([canvasNode('Agent', 'agent', 'gone')], [], known)).toBeNull();
  });

  it.each([null, undefined])('skips the check when the known set is %s (models loading or failed)', set => {
    expect(validateCanvasNodes([canvasNode('Step', 'mcp', 'gone')], [], set)).toBeNull();
  });
});
