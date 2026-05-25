import type { Node } from '@xyflow/react';
import { Background, BackgroundVariant, Controls, MiniMap, ReactFlow } from '@xyflow/react';
import type React from 'react';
import { useCallback } from 'react';
import { AiOutlineApartment } from 'react-icons/ai';
import { useTheme } from '@/contexts/ThemeContext';
import { EDGE_CONFIG } from './constants';
import { nodeTypes } from './Nodes';

const DARK_COLORS: Record<string, string> = {
  mcp: '#38bdf8',
  agent: '#a855f7',
  gate: '#f59e0b',
  cond: '#38bdf8',
  parallel: '#14b8a6',
  router: '#ec4899',
  loop: '#fb923c',
  pool: '#a855f7',
  add: '#374151',
  default: '#374151',
};

const LIGHT_COLORS: Record<string, string> = {
  mcp: '#0284c7',
  agent: '#6d28d9',
  gate: '#d97706',
  cond: '#0284c7',
  parallel: '#0d9488',
  router: '#db2777',
  loop: '#ea580c',
  pool: '#6d28d9',
  add: '#d1d5db',
  default: '#d1d5db',
};

import type { useWorkflowCanvas } from './hooks/useWorkflowCanvas';

interface CanvasViewProps {
  canvas: ReturnType<typeof useWorkflowCanvas>;
  defaultViewport?: { x?: number; y?: number; zoom?: number };
  isReadOnly?: boolean;
}

export const CanvasView: React.FC<CanvasViewProps> = ({ canvas, defaultViewport, isReadOnly }) => {
  const { theme } = useTheme();
  const isDark = theme === 'dark';

  const miniMapNodeColor = useCallback(
    (n: Node): string => {
      const type = n.type ?? 'default';
      const colors = isDark ? DARK_COLORS : LIGHT_COLORS;
      return colors[type] ?? colors.default;
    },
    [isDark],
  );

  // ── ReadOnly Mode Filters ──────────────────────────────────────────────────
  const displayNodes = isReadOnly ? canvas.nodesWithHandlers.filter(n => n.type !== 'add') : canvas.nodesWithHandlers;
  const displayEdges = isReadOnly
    ? canvas.edges.filter(e => {
        const sourceIsAdd = canvas.nodesWithHandlers.find(n => n.id === e.source)?.type === 'add';
        const targetIsAdd = canvas.nodesWithHandlers.find(n => n.id === e.target)?.type === 'add';
        return !sourceIsAdd && !targetIsAdd;
      })
    : canvas.edges;

  const handleNodesChange: typeof canvas.onNodesChange = useCallback(
    changes => {
      if (isReadOnly) {
        // Strip out 'remove' actions (e.g., from Backspace) if read-only
        const safeChanges = changes.filter(c => c.type !== 'remove');
        if (safeChanges.length > 0) canvas.onNodesChange(safeChanges);
      } else {
        canvas.onNodesChange(changes);
      }
    },
    [isReadOnly, canvas.onNodesChange],
  );

  return (
    <div className='flex-1'>
      <ReactFlow
        nodes={displayNodes}
        edges={displayEdges}
        onNodesChange={handleNodesChange}
        onEdgesChange={isReadOnly ? undefined : canvas.onEdgesChange}
        onConnect={isReadOnly ? undefined : canvas.onConnect}
        onNodeClick={canvas.onNodeClick}
        onPaneClick={canvas.onPaneClick}
        nodeTypes={nodeTypes}
        defaultEdgeOptions={EDGE_CONFIG}
        isValidConnection={canvas.isValidConnection}
        defaultViewport={defaultViewport ? { x: defaultViewport.x ?? 0, y: defaultViewport.y ?? 0, zoom: defaultViewport.zoom ?? 1 } : undefined}
        fitView={!defaultViewport}
        fitViewOptions={!defaultViewport ? { padding: 0.1, minZoom: 0.1, maxZoom: 1 } : undefined}
        nodesDraggable={!isReadOnly}
        nodesConnectable={!isReadOnly}
        elementsSelectable={true}
        edgesFocusable={!isReadOnly}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={28}
          size={1}
          color={isDark ? 'rgba(42,51,68,.7)' : 'rgba(15,23,42,.18)'}
        />
        <Controls>
          <button
            title='Auto layout'
            onClick={canvas.runLayout}
            className='flex items-center justify-center w-6 h-6 bg-none border-none cursor-pointer text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text-strong)]'
          >
            <AiOutlineApartment className='-rotate-90' />
          </button>
        </Controls>
        <MiniMap nodeColor={miniMapNodeColor} maskColor={isDark ? 'rgba(11,16,32,.7)' : 'rgba(241,245,249,.8)'} />
      </ReactFlow>
    </div>
  );
};

export default CanvasView;
