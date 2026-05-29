import type { Edge, Node } from '@xyflow/react';
import { useCanvasLayout } from './useCanvasLayout';
import { useCanvasMutations } from './useCanvasMutations';
import { useCanvasNodes } from './useCanvasNodes';
import { useCanvasSelection } from './useCanvasSelection';

export const useWorkflowCanvas = (
  initialNodes?: Node[],
  initialEdges?: Edge[],
  onChange?: () => void,
  onOpenNodePicker?: (nodeId: string) => void,
) => {
  // 1. Core nodes & edges state
  const { nodes, setNodes, edges, setEdges, onNodesChange, onEdgesChange, onConnect, isValidConnection } =
    useCanvasNodes(initialNodes, initialEdges, onChange);

  // 2. Layout & ID generation
  const { generateNodeId, generateEdgeId, runLayout } = useCanvasLayout(nodes, edges, setNodes, setEdges, onChange);

  // 3. Selection & Panel state
  const { selectedNode, setSelected, panelCollapsed, setPanelCollapsed, onNodeClick, onPaneClick, clearSelection } =
    useCanvasSelection(setNodes);

  // 4. Complex Graph Mutations
  const { onNodeDataChange, onDeleteNode, onParallelBranchesChange, onRouterCasesChange, onPick } = useCanvasMutations({
    nodes,
    edges,
    setNodes,
    setEdges,
    setSelected,
    setPanelCollapsed,
    generateNodeId,
    generateEdgeId,
    onChange,
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
    onParallelBranchesChange,
    onRouterCasesChange,
    onPick,
    onOpenNodePicker,
    isValidConnection,
    setPanelCollapsed,
    clearSelection,
  };
};
