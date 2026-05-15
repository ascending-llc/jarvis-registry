import type { SchemaField } from '../types';

interface CELContextReferenceProps {
  upstreamSchema: SchemaField[] | null;
  sourceLabel: string | null;
}

/** CEL Context Reference Component - displays available variables from upstream node. */
const CELContextReference: React.FC<CELContextReferenceProps> = ({ upstreamSchema, sourceLabel }) => {
  if (!upstreamSchema || upstreamSchema.length === 0) {
    return (
      <div className='bg-[rgba(99,102,241,0.06)] border border-[rgba(99,102,241,0.15)] rounded-md p-2.5 mb-3'>
        <div className='font-mono text-[9px] font-bold tracking-wide text-[rgba(99,102,241,0.8)] uppercase mb-1'>
          Available Variables
        </div>
        <div className='text-[10px] text-[var(--jarvis-subtle)] italic'>
          Connect a node to see its output variables here.
        </div>
      </div>
    );
  }
  return (
    <div className='bg-[rgba(99,102,241,0.06)] border border-[rgba(99,102,241,0.15)] rounded-md p-2.5 mb-3'>
      <div className='flex items-center justify-between mb-2'>
        <div className='font-mono text-[9px] font-bold tracking-wide text-[rgba(99,102,241,0.8)] uppercase'>
          Available Variables
        </div>
        {sourceLabel && (
          <div className='text-[9px] text-[var(--jarvis-subtle)] flex items-center gap-1'>
            <span className='opacity-50'>from</span>
            <span className='font-mono text-[var(--jarvis-blue)] font-semibold'>{sourceLabel}</span>
            <span className='font-mono text-[8px] text-[var(--jarvis-subtle)] opacity-60'>/schema</span>
          </div>
        )}
      </div>
      <div className='grid gap-1.5'>
        {upstreamSchema.map((v, i) => (
          <div key={i} className='grid grid-cols-[auto_1fr] gap-2 items-start'>
            <code className='font-mono text-[10px] text-[var(--jarvis-blue)] font-semibold whitespace-nowrap'>
              {v.name}
            </code>
            <div className='text-[10px] text-[var(--jarvis-subtle)] leading-tight'>
              <div className='text-[var(--jarvis-muted)]'>{v.desc}</div>
              <div className='flex gap-2 mt-0.5 flex-wrap'>
                <span className='text-[9px] text-[var(--jarvis-subtle)]'>type: {v.type}</span>
                {v.enum && <span className='text-[9px] text-[var(--jarvis-warning)]'>{v.enum.join(' | ')}</span>}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export { CELContextReference };
export default CELContextReference;
