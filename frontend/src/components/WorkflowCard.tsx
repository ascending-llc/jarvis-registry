import {
  ChartBarIcon,
  ClockIcon,
  PencilSquareIcon,
  QueueListIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { useNavigate } from 'react-router-dom';
import IconButton from '@/components/IconButton';
import UTILS from '@/utils';

/**
 * Temporary interface for Workflow.
 * You should ideally move this to a types file (e.g., src/services/workflow/type.ts)
 */
export interface Workflow {
  id: string;
  name: string;
  type: 'autonomous' | 'supervised';
  description: string;
  lastRunAt?: string;
  runCount: number;
  nodeCount: number;
  enabled: boolean;
  status?: 'active' | 'inactive' | 'error';
  permissions?: {
    VIEW?: boolean;
    EDIT?: boolean;
  };
}

interface WorkflowCardProps {
  workflow: Workflow;
  onToggle?: (id: string, enabled: boolean) => void;
  onEdit?: (workflow: Workflow) => void;
}

const WorkflowCard: React.FC<WorkflowCardProps> = ({
  workflow,
  onToggle,
  onEdit,
}) => {
  const navigate = useNavigate();
  const canEdit = !!workflow.permissions?.EDIT;

  const handleEditClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onEdit) {
      onEdit(workflow);
    } else {
      navigate(`/workflow-edit?id=${workflow.id}`);
    }
  };

  const handleViewClick = () => {
    if (workflow.permissions?.VIEW) {
      navigate(`/workflow-edit?id=${workflow.id}&isReadOnly=true`);
    }
  };

  const handleToggle = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.stopPropagation();
    if (!canEdit) return;
    onToggle?.(workflow.id, e.target.checked);
  };

  const isAutonomous = workflow.type === 'autonomous';

  return (
    <div
      className='group relative flex h-full flex-col rounded-2xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] shadow-sm transition-all duration-300 hover:-translate-y-1 hover:border-[color:var(--jarvis-border-strong)] hover:shadow-xl'
    >


      <div className='p-4 pb-3'>
        {/* Header */}
        <div className='mb-2 flex items-start justify-between gap-2 mt-1'>
          <div className='flex-1 min-w-0'>
            <h3
              onClick={handleViewClick}
              className={`truncate text-[15px] font-semibold text-[var(--jarvis-text)] ${
                workflow.permissions?.VIEW
                  ? 'cursor-pointer transition-colors hover:text-[var(--jarvis-text-strong)]'
                  : ''
              }`}
            >
              {workflow.name}
            </h3>
          </div>

          <div className='flex flex-shrink-0 gap-0.5'>
            {canEdit && (
              <IconButton
                ariaLabel='Edit workflow'
                tooltip='Edit'
                onClick={handleEditClick}
                size='card'
                className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
              >
                <PencilSquareIcon className='h-3.5 w-3.5' />
              </IconButton>
            )}
          </div>
        </div>

        {/* Type Badge */}
        <div className='mb-3'>
          <span
            className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${
              isAutonomous
                ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                : 'bg-[var(--jarvis-info-soft)] text-[var(--jarvis-info-text)]'
            }`}
          >
            {isAutonomous ? 'Autonomous' : 'Supervised'}
          </span>
        </div>

        {/* Description */}
        <p className='mb-3 line-clamp-2 flex-1 text-[12.5px] leading-relaxed text-[var(--jarvis-subtle)]'>
          {workflow.description || 'No description available'}
        </p>

        {/* Meta stats */}
        <div className='mt-3 flex items-center gap-4 border-t border-[color:var(--jarvis-border)] pt-3'>
          <div className='flex items-center gap-1.5'>
            <ClockIcon className='h-[13px] w-[13px] text-[var(--jarvis-muted)]' />
            <span className='text-[12.5px] font-semibold text-[var(--jarvis-text)]'>
              {workflow.lastRunAt ? UTILS.formatTimeSince(workflow.lastRunAt) : 'Never'}
            </span>
            {workflow.lastRunAt && <span className='text-[11px] text-[var(--jarvis-muted)]'>ago</span>}
          </div>
          <div className='flex items-center gap-1.5'>
            <ChartBarIcon className='h-[13px] w-[13px] text-[var(--jarvis-muted)]' />
            <span className='text-[12.5px] font-semibold text-[var(--jarvis-text)]'>
              {workflow.runCount}
            </span>
            <span className='text-[11px] text-[var(--jarvis-muted)]'>runs</span>
          </div>
          <div className='flex items-center gap-1.5'>
            <QueueListIcon className='h-[13px] w-[13px] text-[var(--jarvis-muted)]' />
            <span className='text-[12.5px] font-semibold text-[var(--jarvis-text)]'>
              {workflow.nodeCount}
            </span>
            <span className='text-[11px] text-[var(--jarvis-muted)]'>nodes</span>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className='mt-auto flex items-center justify-between gap-2 rounded-b-2xl border-t border-[color:var(--jarvis-border)] bg-[var(--jarvis-bg)]/70 px-4 py-3'>
        <div className='flex items-center gap-1.5'>
          <div
            className={`h-2 w-2 rounded-full ${
              workflow.enabled
                ? 'bg-[var(--jarvis-success)] shadow-sm shadow-[var(--jarvis-success)]/50'
                : 'bg-[var(--jarvis-faint)]'
            }`}
          />
          <span className='text-xs font-medium text-[var(--jarvis-muted)]'>
            {workflow.enabled ? 'Enabled' : 'Disabled'}
          </span>
        </div>

        <div className='flex items-center gap-2'>
          <label
            className={`relative inline-flex items-center ${
              canEdit ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'
            }`}
            title={canEdit ? 'Toggle workflow status' : 'No edit permission'}
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type='checkbox'
              checked={workflow.enabled}
              onChange={handleToggle}
              disabled={!canEdit}
              className='peer sr-only'
            />
            <div
              className={`relative h-4 w-7 rounded-full transition-colors duration-200 ease-in-out ${
                workflow.enabled ? 'bg-[var(--jarvis-primary)]' : 'bg-[var(--jarvis-faint)]'
              }`}
            >
              <div
                className={`absolute left-0 top-0.5 h-3 w-3 rounded-full bg-white transition-transform duration-200 ease-in-out ${
                  workflow.enabled ? 'translate-x-3.5' : 'translate-x-0.5'
                }`}
              />
            </div>
          </label>
        </div>
      </div>
    </div>
  );
};

export default WorkflowCard;
