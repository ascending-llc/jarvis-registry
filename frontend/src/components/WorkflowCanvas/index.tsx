import type { Node } from '@xyflow/react';
import { Background, BackgroundVariant, Controls, MiniMap, ReactFlow } from '@xyflow/react';
import { forwardRef, useCallback, useImperativeHandle } from 'react';
import { AiOutlineApartment } from 'react-icons/ai';
import { useTheme } from '@/contexts/ThemeContext';
import { EDGE_CONFIG } from './constants';
import { AGENT_SCHEMAS } from './fixtures';
import NodePicker from './NodePicker';
import { nodeTypes } from './Nodes';
import PropsPanel from './PropsPanel';
import type { WorkflowCanvasProps, WorkflowCanvasRef } from './types';
import { useWorkflowCanvas } from './useWorkflowCanvas';

import './index.css';

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

const WorkflowCanvas = forwardRef<WorkflowCanvasRef, WorkflowCanvasProps>(
  ({ workflowId, refreshRunHistoryKey, initialNodes, initialEdges, onSave }, ref) => {
    const { theme } = useTheme();
    const isDark = theme === 'dark';

    const workflow = useWorkflowCanvas(initialNodes, initialEdges);

    useImperativeHandle(ref, () => ({
      save: () => onSave?.(workflow.nodes, workflow.edges),
      getElements: () => ({ nodes: workflow.nodes, edges: workflow.edges }),
    }));

    const miniColor = useCallback(
      (n: Node): string => {
        const type = n.type ?? 'default';
        const colors = isDark ? DARK_COLORS : LIGHT_COLORS;
        return colors[type] ?? colors.default;
      },
      [isDark],
    );

    return (
      <div className='workflow-canvas-root h-full w-full flex flex-col overflow-hidden'>
        <div className='flex-1 flex overflow-hidden'>
          <div className='flex-1'>
            <ReactFlow
              nodes={workflow.nodesWithHandlers}
              edges={workflow.edges}
              onNodesChange={workflow.onNodesChange}
              onEdgesChange={workflow.onEdgesChange}
              onConnect={workflow.onConnect}
              onNodeClick={workflow.onNodeClick}
              onPaneClick={workflow.onPaneClick}
              nodeTypes={nodeTypes}
              defaultEdgeOptions={EDGE_CONFIG}
              isValidConnection={workflow.isValidConnection}
              fitView
              fitViewOptions={{ padding: 0.1, minZoom: 0.1, maxZoom: 1 }}
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
                  onClick={workflow.runLayout}
                  className='flex items-center justify-center w-6 h-6 bg-none border-none cursor-pointer text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text-strong)]'
                >
                  <AiOutlineApartment className='-rotate-90' />
                </button>
              </Controls>
              <MiniMap nodeColor={miniColor} maskColor={isDark ? 'rgba(11,16,32,.7)' : 'rgba(241,245,249,.8)'} />
            </ReactFlow>
          </div>

          <PropsPanel
            workflowId={workflowId}
            refreshRunHistoryKey={refreshRunHistoryKey}
            selectedNode={workflow.selectedNode}
            nodes={workflow.nodes}
            edges={workflow.edges}
            agentSchemas={AGENT_SCHEMAS}
            onOpenAgentPicker={workflow.onOpenAgentPicker}
            onNodeDataChange={workflow.onNodeDataChange}
            onParallelBranchesChange={workflow.onParallelBranchesChange}
            onDeleteNode={workflow.onDeleteNode}
            collapsed={workflow.panelCollapsed}
            onCollapsedChange={workflow.setPanelCollapsed}
          />
        </div>

        {workflow.pickerOpen && (
          <NodePicker
            tab={workflow.pickerTab}
            onTabChange={workflow.setPickerTab}
            onPick={workflow.onPick}
            onClose={() => {
              workflow.setPickerOpen(false);
              workflow.setPendingAdd(null);
            }}
          />
        )}

        {workflow.agentPickerOpen && (
          <NodePicker
            agentOnly
            onPick={(_, agent) => {
              workflow.agentPickerCb?.current?.({ id: agent.id, label: agent.label, desc: agent.desc });
              workflow.setAgentPickerOpen(false);
            }}
            onClose={() => workflow.setAgentPickerOpen(false)}
          />
        )}
      </div>
    );
  },
);

export default WorkflowCanvas;
