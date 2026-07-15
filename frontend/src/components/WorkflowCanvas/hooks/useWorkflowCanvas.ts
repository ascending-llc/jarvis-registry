import type { Edge } from '@xyflow/react';
import type { WorkflowNode } from '../types';
import { useCanvasLayout } from './useCanvasLayout';
import { useCanvasMutations } from './useCanvasMutations';
import { useCanvasNodes } from './useCanvasNodes';
import { useCanvasSelection } from './useCanvasSelection';

export const useWorkflowCanvas = (
  initialNodes?: WorkflowNode[],
  initialEdges?: Edge[],
  onChange?: () => void,
  onOpenNodePicker?: (nodeId: string) => void,
  isReadOnly = false,
) => {
  // 1. Core nodes & edges state
  const { nodes, setNodes, edges, setEdges, onNodesChange, onEdgesChange, onConnect, isValidConnection } =
    useCanvasNodes(initialNodes, initialEdges, onChange, isReadOnly);

  // 2. Layout & ID generation
  const { generateNodeId, generateEdgeId, runLayout } = useCanvasLayout(
    nodes,
    edges,
    setNodes,
    setEdges,
    onChange,
    isReadOnly,
  );

  // 3. Selection & Panel state
  const { selectedNode, setSelected, panelCollapsed, setPanelCollapsed, onNodeClick, onPaneClick, clearSelection } =
    useCanvasSelection(setNodes);

  // 4. Complex Graph Mutations
  const { onNodeDataChange, onDeleteNode, onDeleteEdges, onParallelBranchesChange, onRouterCasesChange, onPick } =
    useCanvasMutations({
      nodes,
      edges,
      setNodes,
      setEdges,
      setSelected,
      setPanelCollapsed,
      generateNodeId,
      generateEdgeId,
      onChange,
      isReadOnly,
    });

  return {
    nodes,
    edges,
    selectedNode,
    panelCollapsed,
    runLayout,
    onNodesChange,
    onEdgesChange,
    onConnect,
    onNodeClick,
    onPaneClick,
    onNodeDataChange,
    onDeleteNode,
    onDeleteEdges,
    onParallelBranchesChange,
    onRouterCasesChange,
    onPick,
    onOpenNodePicker,
    isValidConnection,
    setPanelCollapsed,
    clearSelection,
  };
};
