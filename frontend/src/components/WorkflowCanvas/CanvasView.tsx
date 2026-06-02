import type { Node } from '@xyflow/react';
import { Background, BackgroundVariant, Controls, MiniMap, ReactFlow } from '@xyflow/react';
import type React from 'react';
import { createContext, useCallback, useMemo } from 'react';
import { AiOutlineApartment } from 'react-icons/ai';
import { useTheme } from '@/contexts/ThemeContext';
import { EDGE_CONFIG } from './constants';
import type { useWorkflowCanvas } from './hooks/useWorkflowCanvas';
import { nodeTypes } from './Nodes';

export const CanvasActionsContext = createContext<{ onAdd?: (nodeId: string) => void }>({});

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
  const displayNodes = useMemo(
    () => (isReadOnly ? canvas.nodes.filter(n => n.type !== 'add') : canvas.nodes),
    [isReadOnly, canvas.nodes],
  );
  const displayEdges = useMemo(() => {
    if (!isReadOnly) return canvas.edges;
    const addNodeIds = new Set(canvas.nodes.filter(n => n.type === 'add').map(n => n.id));
    return canvas.edges.filter(e => !addNodeIds.has(e.source) && !addNodeIds.has(e.target));
  }, [isReadOnly, canvas.edges, canvas.nodes]);

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
      <CanvasActionsContext.Provider value={{ onAdd: canvas.onOpenNodePicker }}>
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
          onNodesDelete={nodesToDelete => {
            nodesToDelete.forEach(node => {
              if (node.type !== 'add') {
                canvas.onDeleteNode(node.id);
              }
            });
          }}
          onEdgesDelete={edgesToDelete => {
            canvas.onDeleteEdges(edgesToDelete);
          }}
          defaultViewport={
            defaultViewport
              ? { x: defaultViewport.x ?? 0, y: defaultViewport.y ?? 0, zoom: defaultViewport.zoom ?? 1 }
              : undefined
          }
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
      </CanvasActionsContext.Provider>
    </div>
  );
};

export default CanvasView;
