import type { Node } from '@xyflow/react';
import type React from 'react';
import { useMemo } from 'react';
import { SelectField } from '@/components/FormFields/SelectField';
import type { AgentNodeData, McpNodeData } from '../../types';
import { getAncestors, getEffectiveExecutingParents } from '../../utils/dag';
import { useWorkflowPanel } from '../WorkflowPanelContext';

interface Props {
  node: Node<AgentNodeData | McpNodeData>;
}

export const ExecutionNodeProperties: React.FC<Props> = ({ node }) => {
  const { onNodeDataChange, nodes, edges } = useWorkflowPanel();
  const nodeData = node.data;

  const validRefs = useMemo(() => {
    const ancestors = getAncestors(node.id, edges);
    const effectiveParents = getEffectiveExecutingParents(node.id, edges, nodes);
    const validIds = [...ancestors].filter(id => !effectiveParents.has(id));
    return validIds
      .map(id => nodes.find(n => n.id === id))
      .filter((n): n is Node => Boolean(n) && (n.type === 'agent' || n.type === 'mcp'));
  }, [node.id, edges, nodes]);

  const toggleRef = (refId: string, checked: boolean) => {
    const currentRefs = nodeData.refs || [];
    let nextRefs: string[];
    if (checked) {
      if (!currentRefs.includes(refId)) nextRefs = [...currentRefs, refId];
      else nextRefs = currentRefs;
    } else {
      nextRefs = currentRefs.filter(r => r !== refId);
    }
    onNodeDataChange(node.id, { refs: nextRefs });
  };

  return (
    <>
      <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
        <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
          Executor
        </div>
        <div className='inline-flex items-center gap-1.5 bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-full px-3 py-1 text-xs text-[var(--jarvis-text)]'>
          <span>{node.type === 'mcp' ? '🔌' : '🤖'}</span>
          <span>{nodeData.executorKey}</span>
        </div>
      </div>

      <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
        <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
          Step config
        </div>
        <div className='mb-2'>
          <SelectField
            label='On error'
            options={[
              { value: 'fail', label: 'fail' },
              { value: 'skip', label: 'skip' },
              { value: 'retry', label: 'retry' },
            ]}
            value={'fail'}
            onChange={() => {}}
          />
        </div>
        <div>
          <SelectField
            label='Max retries'
            options={[
              { value: '0', label: '0' },
              { value: '1', label: '1' },
              { value: '3', label: '3' },
            ]}
            value={'0'}
            onChange={() => {}}
          />
        </div>
      </div>

      <div className='px-4 py-3 border-b border-[var(--jarvis-border)] bg-[rgba(29,158,117,0.05)]'>
        <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
          Reference upstream outputs
        </div>
        
        {validRefs.length === 0 ? (
          <div className='text-xs text-[var(--jarvis-subtle)] text-center py-2'>
            No upstream nodes available for reference
          </div>
        ) : (
          <div className='flex flex-col gap-1.5 mt-2'>
            <p className='text-[10px] text-[var(--jarvis-subtle)] mb-2 leading-relaxed border-l-2 border-[#5DCAA5] pl-2'>
              The output of selected nodes will be injected into the beginning of the prompt at runtime.
            </p>
            {validRefs.map(refNode => {
              const isChecked = (nodeData.refs || []).includes(refNode.id);
              return (
                <label
                  key={refNode.id}
                  className='flex items-center gap-2 p-1.5 rounded-md cursor-pointer hover:bg-[rgba(29,158,117,0.12)] transition-colors'
                >
                  <input
                    type='checkbox'
                    className='accent-[#1D9E75] w-3.5 h-3.5 cursor-pointer flex-shrink-0'
                    checked={isChecked}
                    onChange={e => toggleRef(refNode.id, e.target.checked)}
                  />
                  <span className='text-xs text-[var(--jarvis-text-strong)] flex-1 font-medium truncate'>
                    {refNode.data.label as string}
                  </span>
                  <span className='text-[9px] bg-[#9FE1CB] text-[#04342C] px-1.5 py-0.5 rounded-full font-bold uppercase'>
                    {refNode.type}
                  </span>
                </label>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
};
