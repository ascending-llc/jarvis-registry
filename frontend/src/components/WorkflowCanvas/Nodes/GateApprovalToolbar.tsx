import { NodeToolbar, Position } from '@xyflow/react';
import type React from 'react';
import { useEffect, useRef, useState } from 'react';

import { useGlobal } from '@/contexts/GlobalContext';
import type { ResolveRequirementRequest } from '@/services/workflow/type';
import { useWorkflowRuntime } from '../runtime/WorkflowRuntimeContext';
import { RejectFeedbackDialog } from './RejectFeedbackDialog';

interface GateApprovalToolbarProps {
  gateNodeId: string;
}

const _getErrorMessage = (error: unknown): string => {
  if (error instanceof Error && error.message) return error.message;
  if (!error || typeof error !== 'object' || !('detail' in error)) return 'Failed to submit decision';

  const detail = error.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
    return detail.message;
  }
  return 'Failed to submit decision';
};

export const GateApprovalToolbar: React.FC<GateApprovalToolbarProps> = ({ gateNodeId }) => {
  const { activeRun, canControlWorkflow, getPendingConfirmation, resolveRequirement, refetchActiveRun } =
    useWorkflowRuntime();
  const { showToast } = useGlobal();
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [feedbackRequirementKey, setFeedbackRequirementKey] = useState<string | null>(null);
  const [feedback, setFeedback] = useState('');
  const [submittedRequirementKey, setSubmittedRequirementKey] = useState<string | null>(null);

  const requirement = getPendingConfirmation(gateNodeId);
  const requirementKey = requirement && activeRun ? `${activeRun.runId}:${requirement.stepId}` : null;

  useEffect(() => {
    if (!requirementKey || requirementKey !== submittedRequirementKey) {
      setSubmittedRequirementKey(null);
    }
    if (
      !requirementKey ||
      !canControlWorkflow ||
      (feedbackRequirementKey !== null && feedbackRequirementKey !== requirementKey)
    ) {
      setFeedbackRequirementKey(null);
      setFeedback('');
    }
  }, [canControlWorkflow, feedbackRequirementKey, requirementKey, submittedRequirementKey]);

  const submit = async (
    resolution: ResolveRequirementRequest['resolution'],
    rejectionFeedback?: string,
  ): Promise<boolean> => {
    if (!requirement || !requirementKey || submittingRef.current) return false;

    submittingRef.current = true;
    setSubmitting(true);
    try {
      await resolveRequirement(requirement, resolution, rejectionFeedback);
      setSubmittedRequirementKey(requirementKey);
      showToast(resolution === 'confirm' ? 'Approved' : 'Rejected', 'success');
      return true;
    } catch (error: unknown) {
      showToast(_getErrorMessage(error), 'error');
      return false;
    } finally {
      await refetchActiveRun().catch(() => undefined);
      setSubmittedRequirementKey(currentKey => (currentKey === requirementKey ? null : currentKey));
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const handleReject = () => {
    if (!requirement || !requirementKey) return;
    if (requirement.onReject === 'retry') {
      setFeedbackRequirementKey(requirementKey);
      return;
    }
    void submit('reject');
  };

  const handleFeedbackConfirm = async () => {
    const trimmedFeedback = feedback.trim();
    const succeeded = await submit('reject', trimmedFeedback || undefined);
    if (!succeeded) return;
    setFeedbackRequirementKey(null);
    setFeedback('');
  };

  const isVisible =
    canControlWorkflow && requirement !== null && requirementKey !== null && requirementKey !== submittedRequirementKey;
  const isFeedbackDialogOpen =
    feedbackRequirementKey !== null && feedbackRequirementKey === requirementKey && canControlWorkflow;
  const confirmationMessage =
    requirement?.confirmationMessage || 'Review and approve to continue, or reject this step.';

  return (
    <>
      <NodeToolbar
        isVisible={isVisible}
        position={Position.Top}
        offset={12}
        className='nodrag nopan nowheel flex items-center gap-2'
        onPointerDown={event => event.stopPropagation()}
        onClick={event => event.stopPropagation()}
        onDoubleClick={event => event.stopPropagation()}
      >
        <button
          type='button'
          title={confirmationMessage}
          aria-label={`Approve: ${confirmationMessage}`}
          onClick={() => void submit('confirm')}
          disabled={submitting}
          className='rounded-full border border-[var(--jarvis-success)] bg-[var(--jarvis-card)] px-3.5 py-1.5 text-xs font-medium text-[var(--jarvis-success)] shadow-md transition-colors hover:bg-[var(--jarvis-success-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-success)]/30 disabled:cursor-not-allowed disabled:opacity-50'
        >
          {submitting ? 'Submitting...' : 'Approve'}
        </button>
        <button
          type='button'
          title={confirmationMessage}
          aria-label={`Reject: ${confirmationMessage}`}
          onClick={handleReject}
          disabled={submitting}
          className='rounded-full border border-[var(--jarvis-danger)] bg-[var(--jarvis-card)] px-3.5 py-1.5 text-xs font-medium text-[var(--jarvis-danger-text)] shadow-md transition-colors hover:bg-[var(--jarvis-danger-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-danger)]/30 disabled:cursor-not-allowed disabled:opacity-50'
        >
          Reject
        </button>
      </NodeToolbar>

      <RejectFeedbackDialog
        isOpen={isFeedbackDialogOpen}
        feedback={feedback}
        submitting={submitting}
        onFeedbackChange={setFeedback}
        onCancel={() => {
          if (submittingRef.current) return;
          setFeedbackRequirementKey(null);
          setFeedback('');
        }}
        onConfirm={() => void handleFeedbackConfirm()}
      />
    </>
  );
};
