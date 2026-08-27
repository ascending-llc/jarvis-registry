import type { Edge, Node } from '@xyflow/react';
import { describe, expect, it } from 'vitest';
import type { NodeData } from '../types';
import { getAncestors, getEffectiveExecutingParents, getReferenceCandidates, pruneInvalidRefs } from './dag';

const makeNode = (id: string, type: string, refs?: string[]): Node<NodeData> => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data: refs ? { label: id, refs } : { label: id },
});

const makeEdge = (source: string, target: string): Edge => ({ id: `${source}->${target}`, source, target });

describe('getEffectiveExecutingParents', () => {
  it('treats a direct execution-to-execution edge as an implicit parent', () => {
    const nodes = [makeNode('A', 'agent'), makeNode('B', 'agent')];
    const edges = [makeEdge('A', 'B')];

    expect(getEffectiveExecutingParents('B', edges, nodes)).toEqual(new Set(['A']));
  });

  it('returns nothing for a node with no parents', () => {
    const nodes = [makeNode('A', 'agent')];

    expect(getEffectiveExecutingParents('A', [], nodes)).toEqual(new Set());
  });

  it('does not tunnel through a Condition node to reach an implicit parent (regression)', () => {
    // A -> B -> Condition -> C, where C is the first node inside the Condition's branch.
    const nodes = [
      makeNode('A', 'agent'),
      makeNode('B', 'agent'),
      makeNode('Condition', 'cond'),
      makeNode('C', 'agent'),
    ];
    const edges = [makeEdge('A', 'B'), makeEdge('B', 'Condition'), makeEdge('Condition', 'C')];

    expect(getEffectiveExecutingParents('C', edges, nodes)).toEqual(new Set());
  });

  it.each(['router', 'loop', 'parallel'])('does not tunnel through a %s node either', structuralType => {
    const nodes = [makeNode('B', 'agent'), makeNode('S', structuralType), makeNode('C', 'agent')];
    const edges = [makeEdge('B', 'S'), makeEdge('S', 'C')];

    expect(getEffectiveExecutingParents('C', edges, nodes)).toEqual(new Set());
  });

  it('treats the node directly after a branch entry as having that entry as its implicit parent', () => {
    // Condition -> C -> D, both C and D inside the same branch.
    const nodes = [makeNode('Condition', 'cond'), makeNode('C', 'agent'), makeNode('D', 'agent')];
    const edges = [makeEdge('Condition', 'C'), makeEdge('C', 'D')];

    expect(getEffectiveExecutingParents('D', edges, nodes)).toEqual(new Set(['C']));
  });
});

describe('getAncestors', () => {
  it('returns every upstream node reachable through reverse edges, regardless of node type', () => {
    const edges = [makeEdge('A', 'B'), makeEdge('B', 'Condition'), makeEdge('Condition', 'C')];

    expect(getAncestors('C', edges)).toEqual(new Set(['Condition', 'B', 'A']));
  });
});

describe('getReferenceCandidates', () => {
  it('offers every non-implicit execution ancestor for the first node inside a branch (IT Helpdesk workflow regression)', () => {
    // A -> B -> Condition -> C -> D
    const nodes = [
      makeNode('A', 'agent'),
      makeNode('B', 'agent'),
      makeNode('Condition', 'cond'),
      makeNode('C', 'agent'),
      makeNode('D', 'agent'),
    ];
    const edges = [makeEdge('A', 'B'), makeEdge('B', 'Condition'), makeEdge('Condition', 'C'), makeEdge('C', 'D')];

    expect(getReferenceCandidates('C', nodes, edges).map(n => n.id)).toEqual(['A', 'B']);
  });

  it('excludes a direct execution parent as implicit, but still offers earlier nodes', () => {
    // Same graph as above: D's direct parent C is implicit and excluded, A and B remain.
    const nodes = [
      makeNode('A', 'agent'),
      makeNode('B', 'agent'),
      makeNode('Condition', 'cond'),
      makeNode('C', 'agent'),
      makeNode('D', 'agent'),
    ];
    const edges = [makeEdge('A', 'B'), makeEdge('B', 'Condition'), makeEdge('Condition', 'C'), makeEdge('C', 'D')];

    expect(getReferenceCandidates('D', nodes, edges).map(n => n.id)).toEqual(['A', 'B']);
  });

  it('excludes structural nodes and the node itself from candidates', () => {
    const nodes = [makeNode('A', 'agent'), makeNode('Condition', 'cond'), makeNode('C', 'agent')];
    const edges = [makeEdge('A', 'Condition'), makeEdge('Condition', 'C')];

    expect(getReferenceCandidates('C', nodes, edges).map(n => n.id)).toEqual(['A']);
  });
});

describe('pruneInvalidRefs', () => {
  it('strips a ref pointing at a node that is now an implicit (direct execution) parent', () => {
    const nodes = [makeNode('A', 'agent'), makeNode('B', 'agent', ['A'])];
    const edges = [makeEdge('A', 'B')];

    const [, b] = pruneInvalidRefs(nodes, edges);

    expect(b.data.refs).toEqual([]);
  });

  it('strips a ref that is no longer reachable after its edge is removed', () => {
    const nodes = [makeNode('A', 'agent'), makeNode('B', 'agent'), makeNode('C', 'agent', ['A'])];
    const edges = [makeEdge('B', 'C')];

    const pruned = pruneInvalidRefs(nodes, edges);

    expect(pruned.find(n => n.id === 'C')?.data.refs).toEqual([]);
  });

  it('keeps a valid explicit ref across a branch boundary', () => {
    const nodes = [
      makeNode('A', 'agent'),
      makeNode('B', 'agent'),
      makeNode('Condition', 'cond'),
      makeNode('C', 'agent', ['B']),
    ];
    const edges = [makeEdge('A', 'B'), makeEdge('B', 'Condition'), makeEdge('Condition', 'C')];

    const pruned = pruneInvalidRefs(nodes, edges);

    expect(pruned.find(n => n.id === 'C')?.data.refs).toEqual(['B']);
  });
});
