import { ChevronRightIcon, DocumentTextIcon, FolderIcon, FolderOpenIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useMemo, useState } from 'react';

import type { SkillFileMetadata } from '@/services/skill/type';

import { SKILL_MARKDOWN_PATH } from './constants';

export type SkillFileTreeNode =
  | { type: 'file'; name: string; path: string }
  | { type: 'folder'; name: string; path: string; children: SkillFileTreeNode[] };

type MutableFolder = {
  path: string;
  folders: Map<string, MutableFolder>;
  files: Set<string>;
};

const createMutableFolder = (path: string): MutableFolder => ({
  path,
  folders: new Map(),
  files: new Set(),
});

const materializeFolder = (folder: MutableFolder): SkillFileTreeNode[] => {
  const folders: SkillFileTreeNode[] = [...folder.folders.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, child]) => ({
      type: 'folder',
      name,
      path: child.path,
      children: materializeFolder(child),
    }));
  const files: SkillFileTreeNode[] = [...folder.files]
    .sort((left, right) => left.localeCompare(right))
    .map(name => ({ type: 'file', name, path: folder.path ? `${folder.path}/${name}` : name }));
  return [...folders, ...files].sort((left, right) =>
    left.name.localeCompare(right.name, undefined, { sensitivity: 'base' }),
  );
};

export const buildSkillFileTree = (files: SkillFileMetadata[]): SkillFileTreeNode[] => {
  const root = createMutableFolder('');
  root.files.add(SKILL_MARKDOWN_PATH);

  files.forEach(file => {
    const parts = file.relativePath.split('/').filter(Boolean);
    if (parts.length === 0 || file.relativePath === SKILL_MARKDOWN_PATH) return;

    let folder = root;
    parts.slice(0, -1).forEach(part => {
      const path = folder.path ? `${folder.path}/${part}` : part;
      const existing = folder.folders.get(part);
      if (existing) folder = existing;
      else {
        const next = createMutableFolder(path);
        folder.folders.set(part, next);
        folder = next;
      }
    });
    folder.files.add(parts[parts.length - 1]);
  });

  const nodes = materializeFolder(root);
  return nodes.sort((left, right) => {
    if (left.path === SKILL_MARKDOWN_PATH) return -1;
    if (right.path === SKILL_MARKDOWN_PATH) return 1;
    return left.name.localeCompare(right.name, undefined, { sensitivity: 'base' });
  });
};

type SkillFileTreeProps = {
  files: SkillFileMetadata[];
  selectedPath: string;
  onSelect: (path: string) => void;
};

const SkillFileTree: React.FC<SkillFileTreeProps> = ({ files, selectedPath, onSelect }) => {
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const nodes = useMemo(() => buildSkillFileTree(files), [files]);

  const toggleFolder = (path: string) => {
    setExpandedFolders(current => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const renderNode = (node: SkillFileTreeNode, depth: number): React.ReactNode => {
    if (node.type === 'file') {
      const selected = node.path === selectedPath;
      return (
        <button
          type='button'
          key={node.path}
          aria-current={selected ? 'page' : undefined}
          onClick={() => onSelect(node.path)}
          style={{ paddingLeft: `${depth * 14 + 8}px` }}
          className={`flex w-full items-center gap-1.5 rounded-md py-[7px] pr-2 text-left font-mono text-[12.5px] transition ${
            selected
              ? 'bg-[var(--jarvis-primary-soft)] font-medium text-[var(--jarvis-primary-text)]'
              : 'text-[var(--jarvis-muted)] hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-text)]'
          }`}
        >
          <DocumentTextIcon className='h-[13px] w-[13px] flex-shrink-0' />
          <span className='truncate'>{node.name}</span>
        </button>
      );
    }

    const expanded = expandedFolders.has(node.path);
    return (
      <div key={node.path}>
        <button
          type='button'
          aria-expanded={expanded}
          onClick={() => toggleFolder(node.path)}
          style={{ paddingLeft: `${depth * 14 + 6}px` }}
          className='flex w-full items-center gap-1.5 rounded-md py-[7px] pr-2 text-left font-mono text-[12.5px] text-[var(--jarvis-muted)] transition hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-text)]'
        >
          <ChevronRightIcon className={`h-[9px] w-[9px] transition-transform ${expanded ? 'rotate-90' : ''}`} />
          {expanded ? (
            <FolderOpenIcon className='h-[13px] w-[13px] flex-shrink-0' />
          ) : (
            <FolderIcon className='h-[13px] w-[13px] flex-shrink-0' />
          )}
          <span className='truncate'>{node.name}</span>
        </button>
        {expanded && node.children.map(child => renderNode(child, depth + 1))}
      </div>
    );
  };

  return <nav aria-label='Skill files'>{nodes.map(node => renderNode(node, 0))}</nav>;
};

export default SkillFileTree;
