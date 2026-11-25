import {
  ArrowRightIcon,
  ChevronRightIcon,
  DocumentTextIcon,
  EyeSlashIcon,
  FolderIcon,
  FolderOpenIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';

import SERVICES from '@/services';
import type { SkillScanResponse } from '@/services/skill/scanTypes';
import type { SkillFileMetadata } from '@/services/skill/type';

import { SKILL_MARKDOWN_PATH } from './constants';
import type { IndexedScanFinding } from './skillScan';
import {
  getScanLineRange,
  isScanForVersion,
  resolveScanFilePath,
  SCAN_SEVERITIES,
  SCAN_SEVERITY_COLORS,
} from './skillScan';
import { isPreviewableFormat } from './syntaxHighlighting';

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

const getAncestorFolderPaths = (path: string): string[] => {
  const ancestors: string[] = [];
  let currentPath = '';

  for (const segment of path.split('/').filter(Boolean).slice(0, -1)) {
    currentPath = currentPath ? `${currentPath}/${segment}` : segment;
    ancestors.push(currentPath);
  }

  return ancestors;
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

const normalizeValidScanPath = (path: string): string | null => {
  const normalized = path.replace(/\\/g, '/').replace(/^\.\//, '');
  if (!normalized || normalized.startsWith('/') || normalized.includes(':') || normalized.split('/').includes('..')) {
    return null;
  }
  return normalized;
};

type RiskBadgeProps = {
  findings: IndexedScanFinding[];
  locationLabel: string;
  findingsTruncated?: boolean;
  muted?: boolean;
};

const RiskBadge: React.FC<RiskBadgeProps> = ({ findings, locationLabel, findingsTruncated = false, muted = false }) => {
  const severity = SCAN_SEVERITIES.find(level => findings.some(finding => finding.severity === level));
  if (!severity) return null;
  const description = `${findings.length} risk ${findings.length === 1 ? 'finding' : 'findings'} ${locationLabel}${
    findingsTruncated ? ', report truncated' : ''
  }`;
  const color = SCAN_SEVERITY_COLORS[severity];
  return (
    <span
      className={`inline-flex flex-shrink-0 items-center gap-1.5 rounded-full border px-1.5 py-px text-[11px] font-semibold leading-4 ${
        muted
          ? 'border-[var(--jarvis-border)] text-[var(--jarvis-muted)]'
          : 'border-[var(--jarvis-border-strong)] bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)]'
      }`}
      title={description}
      aria-label={description}
    >
      <span
        aria-hidden='true'
        className='h-[7px] w-[7px] rounded-full border'
        style={{ backgroundColor: muted ? 'transparent' : color, borderColor: color }}
      />
      {findings.length}
    </span>
  );
};

type SkillFileTreeProps = {
  files: SkillFileMetadata[];
  scanFindings?: IndexedScanFinding[];
  findingsTruncated?: boolean;
  olderFindings?: boolean;
  selectedPath: string;
  onSelect: (path: string) => void;
  onOpenSecurity?: () => void;
  scan?: SkillScanResponse | null;
  skillId?: string | null;
  skillVersion?: number | null;
  hasUnsavedChanges?: boolean;
  skillMarkdown?: string;
};

const SkillFileTree: React.FC<SkillFileTreeProps> = ({
  files,
  selectedPath,
  onSelect,
  onOpenSecurity,
  scanFindings = [],
  findingsTruncated = false,
  olderFindings = false,
  scan = null,
  skillId = null,
  skillVersion = null,
  hasUnsavedChanges = false,
  skillMarkdown = '',
}) => {
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [unplacedKeys, setUnplacedKeys] = useState<Set<string>>(new Set());
  const [unpreviewablePaths, setUnpreviewablePaths] = useState<Set<string>>(new Set());
  const contentCacheRef = useRef<Map<string, string>>(new Map());
  const availabilityCacheRef = useRef<Map<string, boolean>>(new Map());
  const selectedFileRef = useRef<HTMLButtonElement>(null);
  const nodes = useMemo(() => buildSkillFileTree(files), [files]);
  const fileMetadataByPath = useMemo(() => new Map(files.map(file => [file.relativePath, file])), [files]);
  const { findingsByPath, removedGroups, wholeSkillFindings, invalidPathFindings } = useMemo(() => {
    const paths = [SKILL_MARKDOWN_PATH, ...files.map(file => file.relativePath)];
    const findingsByPath = new Map<string, IndexedScanFinding[]>();
    const removedByPath = new Map<string, IndexedScanFinding[]>();
    const wholeSkillFindings: IndexedScanFinding[] = [];
    let invalidPathFindings = 0;
    for (const finding of scanFindings) {
      if (finding.severity === 'info') continue;
      if (finding.filePath === null) {
        wholeSkillFindings.push(finding);
        continue;
      }
      const path = resolveScanFilePath(finding.filePath, paths);
      if (path) {
        const entries = findingsByPath.get(path);
        if (entries) entries.push(finding);
        else findingsByPath.set(path, [finding]);
        continue;
      }
      const removedPath = normalizeValidScanPath(finding.filePath);
      if (!removedPath) {
        invalidPathFindings++;
        continue;
      }
      const entries = removedByPath.get(removedPath);
      if (entries) entries.push(finding);
      else removedByPath.set(removedPath, [finding]);
    }
    const removedGroups = [...removedByPath.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([path, findings]) => ({ path, findings }));
    return { findingsByPath, removedGroups, wholeSkillFindings, invalidPathFindings };
  }, [files, scanFindings]);

  useEffect(() => {
    contentCacheRef.current.clear();
    availabilityCacheRef.current.clear();
  }, [skillId, skillVersion]);

  // Findings whose reported lines cannot be verified in the current content are surfaced
  // under Whole skill instead of inflating the file and folder counts.
  const canVerifyPlacement = Boolean(skillId && isScanForVersion(scan, skillVersion) && !hasUnsavedChanges);
  useEffect(() => {
    if (!canVerifyPlacement || !skillId) {
      setUnplacedKeys(current => (current.size === 0 ? current : new Set()));
      return;
    }
    let disposed = false;
    const next = new Set<string>();
    const markUnplaced = (finding: IndexedScanFinding, lineCount: number, composedMarkdown: string | null) => {
      if (!getScanLineRange(finding, lineCount, composedMarkdown)) next.add(finding.key);
    };
    const tasks: Promise<void>[] = [];
    for (const [path, entries] of findingsByPath) {
      for (const finding of entries) {
        if (finding.startLine === null) next.add(finding.key);
      }
      const withLines = entries.filter(finding => finding.startLine !== null);
      if (withLines.length === 0) continue;
      if (path === SKILL_MARKDOWN_PATH) {
        const normalized = skillMarkdown.replace(/\r\n/g, '\n');
        const lineCount = normalized.split('\n').length;
        for (const finding of withLines) markUnplaced(finding, lineCount, normalized);
        continue;
      }
      const cacheKey = `${skillId}:${skillVersion}:${path}`;
      const cached = contentCacheRef.current.get(cacheKey);
      if (cached !== undefined) {
        const lineCount = cached.replace(/\r\n/g, '\n').split('\n').length;
        for (const finding of withLines) markUnplaced(finding, lineCount, null);
        continue;
      }
      tasks.push(
        SERVICES.SKILL.getSkillFile(skillId, path)
          .then(file => {
            if (disposed || !file.available || file.isBinary || typeof file.content !== 'string') return;
            contentCacheRef.current.set(cacheKey, file.content);
            const lineCount = file.content.replace(/\r\n/g, '\n').split('\n').length;
            for (const finding of withLines) markUnplaced(finding, lineCount, null);
          })
          .catch(() => undefined),
      );
    }
    void Promise.all(tasks).then(() => {
      if (!disposed) setUnplacedKeys(next);
    });
    return () => {
      disposed = true;
    };
  }, [canVerifyPlacement, findingsByPath, skillId, skillVersion, skillMarkdown]);

  const wholeSkillDisplay = useMemo(
    () => [
      ...wholeSkillFindings,
      ...[...findingsByPath.values()].flat().filter(finding => unplacedKeys.has(finding.key)),
    ],
    [wholeSkillFindings, findingsByPath, unplacedKeys],
  );

  // A file cannot be previewed when any of these holds: content unavailable, binary content,
  // or a format the preview pane does not support. Metadata decides the last two up front;
  // availability and content-level binary flags need the file response.
  useEffect(() => {
    if (!skillId) {
      setUnpreviewablePaths(current => (current.size === 0 ? current : new Set()));
      return;
    }
    let disposed = false;
    const next = new Set<string>();
    const tasks = files
      .filter(file => file.isBinary !== true && isPreviewableFormat(file.relativePath, file.mimeType))
      .map(file => {
        const cacheKey = `${skillId}:${skillVersion}:${file.relativePath}`;
        const cached = availabilityCacheRef.current.get(cacheKey);
        if (cached !== undefined) {
          if (cached) next.add(file.relativePath);
          return Promise.resolve();
        }
        return SERVICES.SKILL.getSkillFile(skillId, file.relativePath)
          .then(response => {
            const blocked = !response.available || response.isBinary === true;
            availabilityCacheRef.current.set(cacheKey, blocked);
            if (blocked) next.add(file.relativePath);
          })
          .catch(() => undefined);
      });
    void Promise.all(tasks).then(() => {
      if (!disposed) setUnpreviewablePaths(next);
    });
    return () => {
      disposed = true;
    };
  }, [files, skillId, skillVersion]);

  useEffect(() => {
    const ancestorPaths = getAncestorFolderPaths(selectedPath);
    if (ancestorPaths.length === 0) return;

    setExpandedFolders(current => {
      if (ancestorPaths.every(path => current.has(path))) return current;
      return new Set([...current, ...ancestorPaths]);
    });
  }, [selectedPath]);

  useEffect(() => {
    selectedFileRef.current?.scrollIntoView({ block: 'nearest' });
  }, [expandedFolders, selectedPath]);

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
      const findings = (findingsByPath.get(node.path) ?? []).filter(finding => !unplacedKeys.has(finding.key));
      const fileMetadata = fileMetadataByPath.get(node.path);
      const cannotPreview =
        fileMetadata?.isBinary === true ||
        !isPreviewableFormat(node.path, fileMetadata?.mimeType) ||
        unpreviewablePaths.has(node.path);
      return (
        <button
          type='button'
          key={node.path}
          ref={selected ? selectedFileRef : undefined}
          aria-current={selected ? 'page' : undefined}
          onClick={() => onSelect(node.path)}
          style={{ paddingLeft: `${depth * 14 + 8}px` }}
          className={`flex w-full items-center gap-1.5 rounded-md py-[7px] pr-2 text-left font-mono text-[12.5px] transition ${
            selected
              ? 'bg-[var(--jarvis-primary-soft)] font-medium text-[var(--jarvis-primary-text)]'
              : 'text-[var(--jarvis-muted)] hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-text)]'
          }`}
        >
          <DocumentTextIcon aria-hidden='true' className='h-[13px] w-[13px] flex-shrink-0' />
          <span className='min-w-0 flex-1 truncate'>{node.name}</span>
          {cannotPreview && (
            <span
              title='This file cannot be previewed.'
              aria-label='Preview unavailable'
              className='inline-flex flex-shrink-0 text-[var(--jarvis-warning)]'
            >
              <EyeSlashIcon aria-hidden='true' className='h-[14px] w-[14px]' />
            </span>
          )}
          <RiskBadge findings={findings} locationLabel='in this file' findingsTruncated={findingsTruncated} />
        </button>
      );
    }

    const expanded = expandedFolders.has(node.path);
    const folderPrefix = `${node.path}/`;
    const findings = [...findingsByPath.entries()].flatMap(([path, entries]) =>
      path.startsWith(folderPrefix) ? entries.filter(finding => !unplacedKeys.has(finding.key)) : [],
    );
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
          <span className='min-w-0 flex-1 truncate'>{node.name}</span>
          <RiskBadge
            findings={findings}
            locationLabel='in this folder'
            findingsTruncated={findingsTruncated}
            muted={expanded}
          />
        </button>
        {expanded && node.children.map(child => renderNode(child, depth + 1))}
      </div>
    );
  };

  return (
    <nav aria-label='Skill files'>
      {nodes.map(node => renderNode(node, 0))}
      {removedGroups.map(({ path, findings }) => (
        <button
          type='button'
          key={path}
          title={`${path} is not present in the current skill. Open Security.`}
          onClick={onOpenSecurity}
          className='flex w-full items-center gap-1.5 rounded-md py-[7px] pl-2 pr-2 text-left font-mono text-[12.5px] text-[var(--jarvis-muted)] transition hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-text)]'
        >
          <DocumentTextIcon aria-hidden='true' className='h-[13px] w-[13px] flex-shrink-0 opacity-60' />
          <span className='min-w-0 flex-1 truncate italic line-through opacity-70'>{path}</span>
          <span className='flex-shrink-0 rounded border border-[var(--jarvis-border-strong)] px-1 text-[10px] not-italic'>
            removed
          </span>
          <RiskBadge
            findings={findings}
            locationLabel={`in removed file ${path}`}
            findingsTruncated={findingsTruncated}
          />
        </button>
      ))}
      {wholeSkillDisplay.length > 0 && (
        <div className='mt-1 border-t border-[var(--jarvis-border)] pt-1'>
          <button
            type='button'
            title='These findings apply to the whole skill or could not be placed on a source line. Open Security.'
            onClick={onOpenSecurity}
            className='flex w-full items-center gap-2 rounded-md py-[7px] px-2 text-left font-mono text-[12.5px] text-[var(--jarvis-muted)] transition hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-text)]'
          >
            <span aria-hidden='true' className='mx-0.5 h-[9px] w-[9px] flex-shrink-0 rotate-45 border border-current' />
            <span className='min-w-0 flex-1 truncate'>Whole skill</span>
            <RiskBadge
              findings={wholeSkillDisplay}
              locationLabel='across the whole skill or unplaced'
              findingsTruncated={findingsTruncated}
            />
            <ArrowRightIcon aria-hidden='true' className='h-[12px] w-[12px] flex-shrink-0' />
          </button>
        </div>
      )}
      {invalidPathFindings > 0 && (
        <div className='px-2 pt-2 text-xs text-[var(--jarvis-muted)]'>
          <p>
            {invalidPathFindings}{' '}
            {invalidPathFindings === 1 ? 'finding has an invalid file path.' : 'findings have invalid file paths.'}
          </p>
          {onOpenSecurity && (
            <button
              type='button'
              className='mt-1 text-[var(--jarvis-primary-text)] hover:underline'
              onClick={onOpenSecurity}
            >
              View in Security
            </button>
          )}
        </div>
      )}
      {olderFindings && scanFindings.length > 0 && (
        <p className='px-2 pt-2 text-xs text-[var(--jarvis-warning-text)]'>Counts are from a previous version.</p>
      )}
      {findingsTruncated && (
        <p className='px-2 pt-2 text-xs text-[var(--jarvis-muted)]'>
          Counts reflect the returned findings. The report is truncated.
        </p>
      )}
    </nav>
  );
};

export default SkillFileTree;
