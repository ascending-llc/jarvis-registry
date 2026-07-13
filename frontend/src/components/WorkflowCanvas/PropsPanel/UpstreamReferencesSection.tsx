import { useMemo } from 'react';
import type { WorkflowNode } from '../types';
import { getReferenceCandidates } from '../utils/dag';
import { useWorkflowPanel } from './WorkflowPanelContext';

interface UpstreamReferencesSectionProps {
  node: WorkflowNode;
}

export const UpstreamReferencesSection = ({ node }: UpstreamReferencesSectionProps) => {
  const { nodes, edges, isReadOnly, onNodeDataChange } = useWorkflowPanel();
  const candidates = useMemo(() => getReferenceCandidates(node.id, nodes, edges), [node.id, nodes, edges]);
  const refs = node.data.refs ?? [];

  const toggleRef = (refId: string, checked: boolean) => {
    if (isReadOnly) return;
    const nextRefs = checked ? (refs.includes(refId) ? refs : [...refs, refId]) : refs.filter(id => id !== refId);
    onNodeDataChange(node.id, { refs: nextRefs });
  };

  return (
    <div className='px-4 py-3 border-b border-[var(--jarvis-border)] bg-[rgba(29,158,117,0.05)]'>
      <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
        Reference upstream outputs
      </div>

      {candidates.length === 0 ? (
        <div className='text-xs text-[var(--jarvis-subtle)] text-center py-2'>
          No upstream nodes available for reference
        </div>
      ) : (
        <div className='flex flex-col gap-1.5 mt-2'>
          <p className='text-[10px] text-[var(--jarvis-subtle)] mb-2 leading-relaxed border-l-2 border-[#5DCAA5] pl-2'>
            The output of selected nodes will be injected into the beginning of the prompt at runtime.
          </p>
          {candidates.map(candidate => (
            <label
              key={candidate.id}
              className={`flex items-center gap-2 p-1.5 rounded-md transition-colors ${
                isReadOnly ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-[rgba(29,158,117,0.12)]'
              }`}
            >
              <input
                type='checkbox'
                className='accent-[#1D9E75] w-3.5 h-3.5 flex-shrink-0 disabled:cursor-not-allowed'
                disabled={isReadOnly}
                checked={refs.includes(candidate.id)}
                onChange={event => toggleRef(candidate.id, event.target.checked)}
              />
              <span className='text-xs text-[var(--jarvis-text-strong)] flex-1 font-medium truncate'>
                {candidate.data.label}
              </span>
              <span className='text-[9px] bg-[#9FE1CB] text-[#04342C] px-1.5 py-0.5 rounded-full font-bold uppercase'>
                {candidate.type}
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
};
