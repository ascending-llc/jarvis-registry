import type React from 'react';
import { useEffect, useState } from 'react';
import type { NodeData, PropsPanelProps } from '../types';
import { PanelHeader } from './PanelHeader';
import { PropertiesContent } from './PropertiesContent';
import { ResizablePanel } from './ResizablePanel';
import { RunHistory } from './RunHistory';
import { useWorkflowPanel } from './WorkflowPanelContext';

/** Node type to icon text mapping for panel context header */
const NODE_TYPE_ICON_MAP: Record<string, string> = {
  mcp: 'MCP',
  agent: 'A2A',
  gate: '⏸',
  cond: 'if',
  parallel: '∥',
  router: '⇄',
  loop: '↻',
  pool: '◈',
};

/** PropsPanel - collapsible properties panel. */
const PropsPanel: React.FC<PropsPanelProps> = ({ panelMode, isNewWorkflow, collapsed = false, onCollapsedChange }) => {
  const [tab, setTab] = useState<'props' | 'hist'>('props');
  const { workflow, selectedNode } = useWorkflowPanel();

  const nodeData = selectedNode?.data as NodeData | undefined;
  const nodeType = selectedNode?.type;

  // Context header data
  const isWorkflow = panelMode === 'workflow';
  const ctxLabel = isWorkflow ? (workflow?.name ?? 'Workflow') : ((nodeData?.label as string | undefined) ?? 'Node');
  const ctxScope = isWorkflow ? 'workflow' : 'node';
  const ctxIconText = isWorkflow ? '⚡' : (NODE_TYPE_ICON_MAP[nodeType ?? 'agent'] ?? 'A2A');

  const isLogicNode = panelMode === 'node' && nodeType !== undefined && !['mcp', 'agent'].includes(nodeType);

  useEffect(() => {
    if (isLogicNode && tab === 'hist') {
      setTab('props');
    }
  }, [isLogicNode, tab]);

  const header = <PanelHeader iconText={ctxIconText} label={ctxLabel} scope={ctxScope} isWorkflow={isWorkflow} />;

  return (
    <ResizablePanel
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange ?? (() => {})}
      header={header}
      tab={tab}
      onTabChange={setTab}
      showHistoryTab={!isLogicNode}
    >
      {tab === 'props' && <PropertiesContent panelMode={panelMode} isNewWorkflow={isNewWorkflow} />}

      {tab === 'hist' && <RunHistory panelMode={panelMode} />}
    </ResizablePanel>
  );
};

export default PropsPanel;
