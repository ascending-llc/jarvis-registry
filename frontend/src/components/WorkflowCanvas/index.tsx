import { ReactFlowProvider, useReactFlow } from '@xyflow/react';
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { CanvasView } from './CanvasView';
import { AGENT_SCHEMAS } from './fixtures';
import { useWorkflowCanvas } from './hooks/useWorkflowCanvas';
import NodePicker from './NodePicker';
import PropsPanel from './PropsPanel';
import { WorkflowPanelProvider } from './PropsPanel/WorkflowPanelContext';
import type { AgentInfo, WorkflowCanvasProps, WorkflowCanvasRef } from './types';

import './index.css';

// Inner component that can use useReactFlow (must be inside ReactFlowProvider)
const WorkflowCanvasInner = forwardRef<WorkflowCanvasRef, WorkflowCanvasProps>(
  (
    {
      workflowId,
      workflow: workflowData,
      refreshRunHistoryKey,
      initialNodes,
      initialEdges,
      isReadOnly,
      isNewWorkflow,
      onDeleteWorkflow,
      onWorkflowChange,
      onSave,
      onChange,
    },
    ref,
  ) => {
    const [panelMode, setPanelMode] = useState<import('./types').PanelMode>('workflow');
    // UI Modal States (moved out of useWorkflowCanvas)
    const [pickerOpen, setPickerOpen] = useState(false);
    const [pickerTab, setPickerTab] = useState('A2A Agents');
    const [pendingAdd, setPendingAdd] = useState<string | null>(null);
    const [agentPickerOpen, setAgentPickerOpen] = useState(false);
    const agentPickerCb = useRef<((agent: AgentInfo) => void) | null>(null);

    const handleOpenNodePicker = (nodeId: string) => {
      if (isReadOnly) return;
      setPendingAdd(nodeId);
      setPickerOpen(true);
    };

    const canvas = useWorkflowCanvas(initialNodes, initialEdges, onChange, handleOpenNodePicker);

    // Switch panel mode based on selection
    useEffect(() => {
      const shouldBeNodeMode = !!canvas.selectedNode;
      const shouldBeWorkflowMode = !canvas.selectedNode && !canvas.panelCollapsed;

      if (shouldBeNodeMode && panelMode !== 'node') {
        setPanelMode('node');
      } else if (shouldBeWorkflowMode && panelMode !== 'workflow') {
        setPanelMode('workflow');
      }
    }, [canvas.selectedNode, canvas.panelCollapsed, panelMode]);

    const reactFlow = useReactFlow();

    useImperativeHandle(ref, () => ({
      save: () => onSave?.(canvas.nodes, canvas.edges, reactFlow.getViewport()),
      getElements: () => ({ nodes: canvas.nodes, edges: canvas.edges }),
      clearSelection: canvas.clearSelection,
      togglePanel: () => {
        if (canvas.panelCollapsed) {
          // Panel collapsed -> Expand panel + switch to workflow mode
          canvas.setPanelCollapsed(false);
          setPanelMode('workflow');
          canvas.clearSelection();
        } else if (panelMode === 'workflow') {
          // Panel expanded + workflow mode -> Collapse panel
          canvas.setPanelCollapsed(true);
        } else {
          // Panel expanded + node mode -> Switch to workflow mode (keep expanded)
          setPanelMode('workflow');
          canvas.clearSelection();
        }
      },
    }));

    const onOpenAgentPicker = (cb: (agent: AgentInfo) => void) => {
      agentPickerCb.current = cb;
      setAgentPickerOpen(true);
    };

    return (
      <div className='workflow-canvas-root h-full w-full flex flex-col overflow-hidden'>
        <div className='flex-1 flex overflow-hidden'>
          <CanvasView canvas={canvas} defaultViewport={workflowData?.canvas?.viewport} isReadOnly={isReadOnly} />

          <WorkflowPanelProvider
            workflowId={workflowId}
            refreshRunHistoryKey={refreshRunHistoryKey}
            workflow={workflowData ?? null}
            selectedNode={canvas.selectedNode}
            nodes={canvas.nodes}
            edges={canvas.edges}
            agentSchemas={AGENT_SCHEMAS}
            onOpenAgentPicker={onOpenAgentPicker}
            onNodeDataChange={canvas.onNodeDataChange}
            onParallelBranchesChange={canvas.onParallelBranchesChange}
            onRouterCasesChange={canvas.onRouterCasesChange}
            onDeleteNode={canvas.onDeleteNode}
            onDeleteWorkflow={onDeleteWorkflow}
            onWorkflowChange={onWorkflowChange}
          >
            <PropsPanel
              panelMode={panelMode}
              isReadOnly={isReadOnly}
              isNewWorkflow={isNewWorkflow}
              collapsed={canvas.panelCollapsed}
              onCollapsedChange={canvas.setPanelCollapsed}
            />
          </WorkflowPanelProvider>
        </div>

        {pickerOpen && pendingAdd && (
          <NodePicker
            tab={pickerTab}
            onTabChange={setPickerTab}
            onPick={(category, item) => {
              setPickerOpen(false);
              canvas.onPick(pendingAdd, category, item);
              setPendingAdd(null);
            }}
            onClose={() => {
              setPickerOpen(false);
              setPendingAdd(null);
            }}
          />
        )}

        {agentPickerOpen && (
          <NodePicker
            agentOnly
            onPick={(_, agent) => {
              agentPickerCb.current?.({
                id: agent.id,
                label: agent.label,
                desc: agent.desc,
                path: (agent as any).executorKey,
              });
              setAgentPickerOpen(false);
            }}
            onClose={() => setAgentPickerOpen(false)}
          />
        )}
      </div>
    );
  },
);

// Outer wrapper that provides ReactFlowProvider
const WorkflowCanvas = forwardRef<WorkflowCanvasRef, WorkflowCanvasProps>((props, ref) => (
  <ReactFlowProvider>
    <WorkflowCanvasInner {...props} ref={ref} />
  </ReactFlowProvider>
));

export default WorkflowCanvas;
