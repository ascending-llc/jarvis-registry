import { StarIcon } from '@heroicons/react/24/solid';
import type React from 'react';

import type { ModelGatewaySelection, ModelSourceListItem } from '@/services/model/type';

type ModelDefaultBadgeProps = {
  model: Pick<ModelSourceListItem, 'id' | 'mode'>;
  selection: ModelGatewaySelection;
};

const ModelDefaultBadge: React.FC<ModelDefaultBadgeProps> = ({ model, selection }) => {
  const isDefault =
    model.mode === 'chat'
      ? selection.defaultWorkflowModelSourceId === model.id
      : selection.embeddingModelSourceId === model.id;
  if (!isDefault) return null;

  return (
    <span className='inline-flex flex-shrink-0 items-center gap-1 rounded-full bg-[var(--jarvis-primary-soft)] px-2 py-[3px] text-[10.5px] font-semibold leading-none text-[var(--jarvis-primary-text)]'>
      <StarIcon className='h-3 w-3' />
      {model.mode === 'chat' ? 'Default workflow' : 'Default embedding'}
    </span>
  );
};

export default ModelDefaultBadge;
