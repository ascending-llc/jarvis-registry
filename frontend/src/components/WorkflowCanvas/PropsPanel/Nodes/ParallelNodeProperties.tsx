import type { Node } from '@xyflow/react';
import type React from 'react';
import type { ParallelNodeData } from '../../types';
import { BranchList } from '../shared';
import { useWorkflowPanel } from '../WorkflowPanelContext';

/** ParallelNodeProperties - handles Parallel node configuration with unlimited branches. */
export const ParallelNodeProperties: React.FC<{ node: Node<ParallelNodeData> }> = ({ node }) => {
  const { isReadOnly, onNodeDataChange, onParallelBranchesChange } = useWorkflowPanel();
  const nodeData = node.data as ParallelNodeData;

  const parBranches = Array.isArray(nodeData?.branches) ? nodeData.branches : ['Branch A', 'Branch B'];

  const addPar = (val: string) => {
    const next = [...parBranches, val];
    onParallelBranchesChange?.(node.id, parBranches, next);
  };

  const rmPar = (i: number) => {
    const next = parBranches.filter((_, j) => j !== i);
    onParallelBranchesChange?.(node.id, parBranches, next);
  };

  const updatePar = (i: number, val: string) => {
    const next = [...parBranches];
    next[i] = val;
    // For updating text, we just update data, no layout rebuild needed
    onNodeDataChange?.(node.id, { branches: next });
  };

  return (
    <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
      <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
        Branches <span className='text-[var(--jarvis-subtle)] font-normal'>(no limit)</span>
      </div>
      <BranchList
        items={parBranches}
        onAdd={() => {
          const L = 'ABCDEFGHIJKLMNOP';
          addPar(`Branch ${L[parBranches.length] || parBranches.length}`);
        }}
        onRm={i => parBranches.length > 1 && rmPar(i)}
        onChange={updatePar}
        addLabel='+ Add branch'
        disabled={isReadOnly}
      />
      <p className='text-[11px] text-[var(--jarvis-subtle)] mt-1.5 leading-relaxed'>
        Each branch fans out independently. Add the next node after each branch output on the canvas.
      </p>
    </div>
  );
};

export default ParallelNodeProperties;
