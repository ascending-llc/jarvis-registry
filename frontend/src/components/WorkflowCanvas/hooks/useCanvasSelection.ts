import type { Node } from '@xyflow/react';
import { useCallback, useState } from 'react';
import type { WorkflowNode } from '../types';

export const useCanvasSelection = (setNodes: React.Dispatch<React.SetStateAction<WorkflowNode[]>>) => {
  const [selectedNode, setSelected] = useState<WorkflowNode | null>(null);
  const [panelCollapsed, setPanelCollapsed] = useState(false);

  const onNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    const workflowNode = node as WorkflowNode;
    setSelected(prev => {
      const isSame = prev?.id === workflowNode.id;
      if (isSame) {
        setPanelCollapsed(c => !c);
        return prev;
      }
      setPanelCollapsed(false);
      return workflowNode;
    });
  }, []);

  const clearSelection = useCallback(() => {
    setSelected(null);
    setNodes(nds => nds.map(n => ({ ...n, selected: false })));
  }, [setNodes]);

  const onPaneClick = useCallback(() => {
    clearSelection();
  }, [clearSelection]);

  return {
    selectedNode,
    setSelected,
    panelCollapsed,
    setPanelCollapsed,
    onNodeClick,
    onPaneClick,
    clearSelection,
  };
};
