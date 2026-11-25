import type { Element, Parents, Root } from 'hast';
import { groupScanFindingsByLocation, type LocatedScanFinding } from './skillScan';

const BLOCK_TAGS = new Set(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'pre', 'tr', 'table', 'hr']);
const SCAN_KEYS = 'dataScanFindingKeys';

type Anchor = {
  node: Element;
  parent: Parents;
  index: number;
  start: number;
  end: number;
  depth: number;
  keys: string[];
};

const getAnchors = (root: Root, lineOffset: number) => {
  const anchors: Anchor[] = [];
  const walk = (parent: Parents, depth: number) => {
    parent.children.forEach((node, index) => {
      if (node.type !== 'element') return;
      if (node.position && BLOCK_TAGS.has(node.tagName)) {
        anchors.push({
          node,
          parent,
          index,
          start: node.position.start.line + lineOffset,
          end: node.position.end.line + lineOffset,
          depth,
          keys: [],
        });
      }
      walk(node, depth + 1);
    });
  };
  walk(root, 0);
  return anchors;
};

export const createMarkdownScanPlugin = (findings: LocatedScanFinding[], lineOffset: number) => () => (tree: Root) => {
  const anchors = getAnchors(tree, lineOffset);
  for (const {
    findings: entries,
    range: [start, end],
  } of groupScanFindingsByLocation(findings)) {
    let best: Anchor | undefined;
    for (const candidate of anchors) {
      if (candidate.start > end || candidate.end < start) continue;
      if (!best) {
        best = candidate;
        continue;
      }
      // Prefer a block containing the start line, then the smallest rendered block.
      const firstLine = Math.max(candidate.start, start);
      const bestLine = Math.max(best.start, start);
      const span = candidate.end - candidate.start;
      const bestSpan = best.end - best.start;
      if (
        firstLine < bestLine ||
        (firstLine === bestLine && (span < bestSpan || (span === bestSpan && candidate.depth > best.depth)))
      ) {
        best = candidate;
      }
    }
    best?.keys.push(...entries.map(({ finding }) => finding.key));
  }

  for (const { node, parent, index, keys } of anchors.reverse()) {
    if (!keys.length) continue;
    const properties = { [SCAN_KEYS]: keys.join(' ') };
    if (node.tagName === 'pre') {
      Object.assign(node.properties, properties);
    } else if (node.tagName === 'li' || node.tagName === 'tr') {
      // Keep list items and table cells as direct children of their required parents.
      const container = node.tagName === 'li' ? node : node.children.find(child => child.type === 'element');
      if (container?.type !== 'element') continue;
      container.children = [{ type: 'element', tagName: 'div', properties, children: container.children }];
    } else {
      parent.children[index] = { type: 'element', tagName: 'div', properties, children: [node] };
    }
  }
};

export const getMarkdownNodeFindings = (
  node: Element | undefined,
  findings: ReadonlyMap<string, LocatedScanFinding>,
) => {
  const keys = node?.properties[SCAN_KEYS];
  if (typeof keys !== 'string') return [];
  return keys.split(' ').flatMap(key => {
    const entry = findings.get(key);
    return entry ? [entry] : [];
  });
};
