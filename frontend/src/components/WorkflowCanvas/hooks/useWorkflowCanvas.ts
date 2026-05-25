import type { Edge, Node } from '@xyflow/react';
import { useMemo } from 'react';
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
  const { syncIdCounters, generateNodeId, generateEdgeId, runLayout } = useCanvasLayout(
    nodes,
    edges,
    setNodes,
    setEdges,
    onChange,
  );

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
    syncIdCounters,
    generateNodeId,
    generateEdgeId,
    onChange,
  });

  // 5. Inject handlers into 'add' nodes
  const nodesWithHandlers = useMemo(
    () =>
      nodes.map(n =>
        n.type === 'add'
          ? {
              ...n,
              data: {
                ...n.data,
                onAdd: () => {
                  onOpenNodePicker?.(n.id);
                },
              },
            }
          : n,
      ),
    [nodes, onOpenNodePicker],
  );

  return {
    nodes,
    edges,
    nodesWithHandlers,
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
    isValidConnection,
    setPanelCollapsed,
    clearSelection,
  };
};
