import { CheckIcon, PlayIcon } from '@heroicons/react/24/outline';
import type { Edge, Node } from '@xyflow/react';
import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import WorkflowCanvas from '@/components/WorkflowCanvas';
import { apiNodesToCanvas, canvasToApiNodes } from '@/components/WorkflowCanvas/convert';
import type { WorkflowCanvasRef } from '@/components/WorkflowCanvas/types';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import type { Workflow } from '@/services/workflow/type';

const WorkflowRegistryOrEdit: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { showToast } = useGlobal();
  const { refreshWorkflowData, handleWorkflowUpdate } = useServer();

  const id = searchParams.get('id');
  const isReadOnly = searchParams.get('isReadOnly') === 'true';
  const isEditMode = !!id;

  const canvasRef = useRef<WorkflowCanvasRef>(null);
  const titleInputRef = useRef<HTMLInputElement>(null);

  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [saving, setSaving] = useState(false);
  const [triggering, setTriggering] = useState(false);

  // Editable title — seeded from URL param immediately, then overwritten when detail loads
  const [titleValue, setTitleValue] = useState(searchParams.get('name') ?? 'New Workflow');
  const [titleSaving, setTitleSaving] = useState(false);

  // ── Sync title once workflow detail is loaded ──────────────────────────────
  useEffect(() => {
    if (workflow?.name) setTitleValue(workflow.name);
  }, [workflow?.name]);

  // ── Load existing workflow when editing ────────────────────────────────────
  useEffect(() => {
    if (id) getDetail(id);
  }, [id]);

  const getDetail = async (workflowId: string) => {
    setLoadingDetail(true);
    try {
      const data = await SERVICES.WORKFLOW.getWorkflowDetail(workflowId);
      setWorkflow(data);
    } catch (error: any) {
      showToast(error?.detail?.message || 'Failed to fetch workflow', 'error');
    } finally {
      setLoadingDetail(false);
    }
  };

  // ── Derive initial canvas elements from loaded workflow ────────────────────
  const { nodes: initialNodes, edges: initialEdges } = (() => {
    if (!isEditMode || !workflow) return { nodes: undefined, edges: undefined };
    const { nodes, edges } = apiNodesToCanvas(workflow.nodes ?? []);
    return { nodes: nodes as Node[], edges };
  })();

  // ── Inline title rename ────────────────────────────────────────────────────
  const handleTitleSave = async () => {
    const trimmed = titleValue.trim();
    if (!trimmed) {
      setTitleValue(workflow?.name ?? 'New Workflow');
      return;
    }
    // Nothing changed or not yet persisted — skip API call
    if (!id || trimmed === workflow?.name) return;

    setTitleSaving(true);
    try {
      await SERVICES.WORKFLOW.updateWorkflow(id, { name: trimmed });
      setWorkflow(prev => (prev ? { ...prev, name: trimmed } : prev));
      handleWorkflowUpdate(id, { name: trimmed });
      showToast('Workflow renamed', 'success');
    } catch (error: any) {
      const msg = error?.detail?.message || 'Failed to rename workflow';
      showToast(msg, 'error');
      setTitleValue(workflow?.name ?? trimmed);
    } finally {
      setTitleSaving(false);
    }
  };

  const handleTitleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      titleInputRef.current?.blur();
    }
    if (e.key === 'Escape') {
      setTitleValue(workflow?.name ?? 'New Workflow');
      titleInputRef.current?.blur();
    }
  };

  // ── Save canvas ────────────────────────────────────────────────────────────
  const handleSave = async (nodes: Node[], edges: Edge[]) => {
    const apiNodes = canvasToApiNodes(nodes as Parameters<typeof canvasToApiNodes>[0], edges);
    if (apiNodes.length === 0) {
      showToast('Add at least one node before saving', 'error');
      return;
    }
    // Validate step nodes have a non-empty executor key or agent pool
    const invalidSteps = apiNodes.flatMap(function collect(n): typeof apiNodes {
      const children = n.children ?? [];
      if (n.nodeType === 'step' && !n.executorKey && (!n.a2aPool || n.a2aPool.length === 0)) {
        return [n, ...children.flatMap(collect)];
      }
      return children.flatMap(collect);
    });
    if (invalidSteps.length > 0) {
      showToast(`Node "${invalidSteps[0].name}" requires an executor key or agent pool`, 'error');
      return;
    }
    setSaving(true);
    try {
      if (isEditMode && id) {
        const updated = await SERVICES.WORKFLOW.updateWorkflow(id, { nodes: apiNodes });
        handleWorkflowUpdate(id, { nodeCount: updated.numNodes ?? apiNodes.length });
        showToast('Workflow updated successfully!', 'success');
      } else {
        await SERVICES.WORKFLOW.createWorkflow({ name: titleValue.trim() || 'New Workflow', nodes: apiNodes });
        await refreshWorkflowData();
        showToast('Workflow created successfully!', 'success');
        navigate('/?tab=workflow', { replace: true });
      }
    } catch (error: any) {
      const msg = error?.detail?.message || (typeof error?.detail === 'string' ? error.detail : '');
      showToast(msg || 'Failed to save workflow', 'error');
    } finally {
      setSaving(false);
    }
  };

  // ── Trigger run ────────────────────────────────────────────────────────────
  const handleTrigger = async () => {
    if (!id) {
      showToast('Save the workflow before triggering a run', 'error');
      return;
    }
    setTriggering(true);
    try {
      await SERVICES.WORKFLOW.triggerWorkflowRun(id, {});
      showToast('Workflow run triggered!', 'success');
    } catch (error: any) {
      const msg = error?.detail?.message || (typeof error?.detail === 'string' ? error.detail : '');
      showToast(msg || 'Failed to trigger workflow run', 'error');
    } finally {
      setTriggering(false);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    // Negative margins cancel out Layout's px-4 sm:px-6 lg:px-8 pt-4 md:pt-8 pb-1 md:pb-2
    <div
      className='-mx-4 sm:-mx-6 lg:-mx-8 -mt-4 md:-mt-8 -mb-1 md:-mb-2'
      style={{ height: 'calc(100% + 2.25rem)', display: 'flex', flexDirection: 'column' }}
    >
      {/* ── Page Header ─────────────────────────────────────────────────────── */}
      <div
        className='flex items-center justify-between px-5 border-b border-[color:var(--jarvis-border)] bg-[var(--jarvis-surface)]'
        style={{ height: 48, flexShrink: 0 }}
      >
        {/* Editable title */}
        <div className='flex items-center gap-1.5 min-w-0 flex-1 mr-4'>
          {isReadOnly ? (
            <span className='text-sm font-semibold text-[var(--jarvis-text-strong)] tracking-tight truncate'>
              {titleValue}
            </span>
          ) : (
            <>
              <input
                ref={titleInputRef}
                value={titleValue}
                onChange={e => setTitleValue(e.target.value)}
                onBlur={handleTitleSave}
                onKeyDown={handleTitleKeyDown}
                disabled={titleSaving || loadingDetail}
                className='min-w-0 flex-1 max-w-xs bg-transparent text-sm font-semibold text-[var(--jarvis-text-strong)] tracking-tight outline-none border-b border-transparent hover:border-[color:var(--jarvis-border)] focus:border-[color:var(--jarvis-primary)] transition-colors px-0.5 disabled:opacity-60 disabled:cursor-not-allowed'
              />
              {titleSaving && (
                <span className='h-3 w-3 animate-spin rounded-full border-b-2 border-[var(--jarvis-primary)] flex-shrink-0' />
              )}
            </>
          )}
          {isReadOnly && (
            <span className='ml-1 flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium bg-[var(--jarvis-info-soft)] text-[var(--jarvis-info-text)]'>
              View only
            </span>
          )}
        </div>

        {!isReadOnly && (
          <div className='flex items-center gap-2 flex-shrink-0'>
            <button
              onClick={handleTrigger}
              disabled={triggering || !id}
              className='inline-flex items-center gap-1 px-2.5 py-1 border border-transparent rounded-md text-xs font-medium text-white bg-[var(--jarvis-primary)] hover:opacity-90 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed'
            >
              {triggering ? (
                <span className='h-3.5 w-3.5 animate-spin rounded-full border-b-2 border-white' />
              ) : (
                <PlayIcon className='h-3.5 w-3.5' />
              )}
              Trigger run
            </button>

            <button
              onClick={() => canvasRef.current?.save()}
              disabled={saving || loadingDetail}
              className='inline-flex items-center justify-center gap-1 px-2.5 py-1 border border-transparent rounded-md text-xs font-medium text-white bg-[var(--jarvis-primary-hover)] hover:opacity-90 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed'
            >
              {saving ? (
                <span className='h-3.5 w-3.5 animate-spin rounded-full border-b-2 border-white' />
              ) : (
                <CheckIcon className='h-3.5 w-3.5' />
              )}
              {isEditMode ? 'Update' : 'Save'}
            </button>
          </div>
        )}
      </div>

      {/* ── Canvas ──────────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        {loadingDetail ? (
          <div className='flex h-full items-center justify-center'>
            <div className='h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-primary)]' />
          </div>
        ) : (
          // key forces canvas remount when switching between workflows
          <WorkflowCanvas
            key={id ?? 'new'}
            ref={canvasRef}
            workflowId={id ?? undefined}
            initialNodes={initialNodes}
            initialEdges={initialEdges}
            onSave={handleSave}
          />
        )}
      </div>
    </div>
  );
};

export default WorkflowRegistryOrEdit;
