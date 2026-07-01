import { CheckIcon, CogIcon, PlayIcon } from '@heroicons/react/24/outline';
import type { Edge, Node } from '@xyflow/react';
import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useBlocker, useNavigate, useSearchParams } from 'react-router-dom';
import WorkflowCanvas from '@/components/WorkflowCanvas';
import { apiNodesToCanvas, canvasToApiNodes, validateApiNodes } from '@/components/WorkflowCanvas/convert';
import type { WorkflowCanvasRef } from '@/components/WorkflowCanvas/types';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import type { Workflow, WorkflowNode as ApiWorkflowNode } from '@/services/workflow/type';
import DeleteWorkflowDialog from './DeleteWorkflowDialog';
import TriggerRunModal from './TriggerRunModal';
import UnsavedChangesDialog from './UnsavedChangesDialog';

type MutatingAction = 'idle' | 'saving' | 'triggering' | 'deleting';

const WorkflowRegistryOrEdit: React.FC = () => {
  // ── 1. Context & Routing ─────────────────────────────────────────────────────────
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { showToast } = useGlobal();
  const { refreshWorkflowData, handleWorkflowUpdate } = useServer();

  const id = searchParams.get('id');
  const isReadOnly = searchParams.get('isReadOnly') === 'true';
  const isEditMode = !!id;
  const canvasRef = useRef<WorkflowCanvasRef>(null);

  // ── 2. Resource State ────────────────────────────────────────────────────────────
  const [workflow, setWorkflow] = useState<Partial<Workflow> | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // ── 3. Mutating Action (State Machine) ─────────────────────────────────────────
  const [mutatingAction, setMutatingAction] = useState<MutatingAction>('idle');

  // ── 4. Dirty Checking & UI State ───────────────────────────────────────────────
  const [_hasChanges, _setHasChanges] = useState(false);
  const hasChangesRef = useRef(false);
  const setHasChanges = (val: boolean) => {
    hasChangesRef.current = val;
    _setHasChanges(val);
  };
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [triggerModalOpen, setTriggerModalOpen] = useState(false);
  const [runHistoryRefresh, setRunHistoryRefresh] = useState(0);

  // ── Side Effects: Block navigation & BeforeUnload ──────────────────────────────
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    if (isReadOnly) return false;
    const currentUrl = currentLocation.pathname + currentLocation.search;
    const nextUrl = nextLocation.pathname + nextLocation.search;
    return hasChangesRef.current && currentUrl !== nextUrl;
  });

  useEffect(() => {
    if (isReadOnly) return;
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (hasChangesRef.current) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [isReadOnly]);

  // ── Side Effects: Save shortcut (Cmd+S / Ctrl+S) ───────────────────────────────
  useEffect(() => {
    if (isReadOnly || mutatingAction !== 'idle') return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        canvasRef.current?.save();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isReadOnly, mutatingAction]);

  // ── Fetch Initial Data ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (id) getDetail(id);
    else {
      setWorkflow({
        name: searchParams.get('name') ?? 'New Workflow',
        description: '',
      });
    }
  }, [id, searchParams]);

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
  const { nodes: initialNodes, edges: initialEdges } = useMemo(() => {
    if (!isEditMode || !workflow) return { nodes: undefined, edges: undefined };
    const { nodes, edges } = apiNodesToCanvas(workflow.nodes ?? []);
    return { nodes: nodes as Node[], edges };
  }, [isEditMode, workflow]);

  // ── Actions: Save ────────────────────────────────────────────────────────────
  const handleSave = async (nodes: Node[], edges: Edge[], viewport: { x: number; y: number; zoom: number }) => {
    const apiNodes = canvasToApiNodes(nodes as unknown as Parameters<typeof canvasToApiNodes>[0], edges);
    if (apiNodes.length === 0) {
      showToast('Add at least one node before saving', 'error');
      return;
    }

    const validationError = validateApiNodes(apiNodes);
    if (validationError) {
      showToast(validationError, 'error');
      return;
    }

    // validateApiNodes guarantees no unresolved gate placeholders remain past this point.
    const validatedNodes = apiNodes as unknown as ApiWorkflowNode[];

    setMutatingAction('saving');

    try {
      if (isEditMode && id) {
        const updated = await SERVICES.WORKFLOW.updateWorkflow(id, {
          name: workflow?.name,
          description: workflow?.description,
          nodes: validatedNodes,
          canvas: { viewport },
        });
        handleWorkflowUpdate(id, { nodeCount: updated.numNodes ?? validatedNodes.length, name: workflow?.name });
        setHasChanges(false);
        showToast('Workflow updated successfully!', 'success');
      } else {
        await SERVICES.WORKFLOW.createWorkflow({
          name: workflow?.name?.trim() || 'New Workflow',
          description: workflow?.description?.trim() || undefined,
          nodes: validatedNodes,
          canvas: { viewport },
        });
        setHasChanges(false);
        await refreshWorkflowData();
        showToast('Workflow created successfully!', 'success');
        navigate('/?tab=workflow', { replace: true });
      }
    } catch (error: any) {
      const msg = error?.detail?.message || (typeof error?.detail === 'string' ? error.detail : '');
      showToast(msg || 'Failed to save workflow', 'error');
    } finally {
      setMutatingAction('idle');
    }
  };

  // ── Actions: Trigger run ─────────────────────────────────────────────────────
  const handleTrigger = async (initialInput: Record<string, any> = {}) => {
    if (!id) {
      showToast('Save the workflow before triggering a run', 'error');
      return;
    }
    setTriggerModalOpen(false);
    setMutatingAction('triggering');
    try {
      await SERVICES.WORKFLOW.triggerWorkflowRun(id, { initialInput });
      setRunHistoryRefresh(k => k + 1);
      showToast('Workflow run triggered!', 'success');
    } catch (error: any) {
      const msg = error?.detail?.message || (typeof error?.detail === 'string' ? error.detail : '');
      showToast(msg || 'Failed to trigger workflow run', 'error');
    } finally {
      setMutatingAction('idle');
    }
  };

  // ── Actions: Workflow metadata change (from PropsPanel) ──────────────────────
  const handleWorkflowChange = (patch: Partial<Pick<Workflow, 'name' | 'description'>>) => {
    setWorkflow(prev => (prev ? { ...prev, ...patch } : prev));
    setHasChanges(true);
  };

  // ── Actions: Delete workflow ─────────────────────────────────────────────────
  const handleDeleteWorkflow = async () => {
    if (!id) return;

    setMutatingAction('deleting');
    try {
      await SERVICES.WORKFLOW.deleteWorkflow(id);
      await refreshWorkflowData();
      showToast('Workflow deleted', 'success');
      setHasChanges(false);
      navigate('/?tab=workflow', { replace: true });
    } catch (error: any) {
      const msg = error?.detail?.message || 'Failed to delete workflow';
      showToast(msg, 'error');
      setDeleteDialogOpen(false);
    } finally {
      setMutatingAction('idle');
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
        {/* Title */}
        <div className='flex items-center gap-1.5 min-w-0 flex-1 mr-4'>
          <span className='text-sm font-semibold text-[var(--jarvis-text-strong)] tracking-tight truncate'>
            {workflow?.name ?? 'New Workflow'}
          </span>

          {/* Settings button */}
          <button
            type='button'
            onClick={() => canvasRef.current?.togglePanel()}
            disabled={loadingDetail}
            title='Workflow settings'
            className='flex-shrink-0 p-1 rounded-md text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text-strong)] hover:bg-[var(--jarvis-card-muted)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
          >
            <CogIcon className='h-4 w-4' />
          </button>

          {isReadOnly && (
            <span className='ml-1 flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium bg-[var(--jarvis-info-soft)] text-[var(--jarvis-info-text)]'>
              View only
            </span>
          )}
        </div>

        {!isReadOnly && (
          <div className='flex items-center gap-2 flex-shrink-0'>
            <button
              onClick={() => setTriggerModalOpen(true)}
              disabled={mutatingAction !== 'idle' || !id}
              className='inline-flex items-center gap-1 px-2.5 py-1 border border-transparent rounded-md text-xs font-medium text-white bg-[var(--jarvis-primary)] hover:opacity-90 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed'
            >
              {mutatingAction === 'triggering' ? (
                <span className='h-3.5 w-3.5 animate-spin rounded-full border-b-2 border-white' />
              ) : (
                <PlayIcon className='h-3.5 w-3.5' />
              )}
              Trigger run
            </button>

            <button
              onClick={() => canvasRef.current?.save()}
              disabled={mutatingAction !== 'idle' || loadingDetail}
              className='inline-flex items-center justify-center gap-1 px-2.5 py-1 border border-transparent rounded-md text-xs font-medium text-white bg-[var(--jarvis-primary-hover)] hover:opacity-90 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed'
            >
              {mutatingAction === 'saving' ? (
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
            workflow={workflow}
            refreshRunHistoryKey={runHistoryRefresh}
            initialNodes={initialNodes}
            initialEdges={initialEdges}
            isReadOnly={isReadOnly}
            isNewWorkflow={!isEditMode}
            onDeleteWorkflow={() => setDeleteDialogOpen(true)}
            onWorkflowChange={handleWorkflowChange}
            onSave={handleSave}
            onChange={() => setHasChanges(true)}
          />
        )}
      </div>

      {/* ── Unsaved changes confirmation dialog ─────────────────────────────────── */}
      <UnsavedChangesDialog
        isOpen={blocker.state === 'blocked'}
        onCancel={() => blocker.reset?.()}
        onConfirm={() => blocker.proceed?.()}
      />

      {/* ── Delete workflow confirmation dialog ─────────────────────────────────── */}
      <DeleteWorkflowDialog
        isOpen={deleteDialogOpen}
        workflowName={workflow?.name ?? 'New Workflow'}
        deleting={mutatingAction === 'deleting'}
        onCancel={() => setDeleteDialogOpen(false)}
        onConfirm={handleDeleteWorkflow}
      />

      {/* ── Trigger run modal ─────────────────────────────────────────────────── */}
      <TriggerRunModal
        isOpen={triggerModalOpen}
        workflowName={workflow?.name ?? ''}
        onClose={() => setTriggerModalOpen(false)}
        onTrigger={handleTrigger}
        triggering={mutatingAction === 'triggering'}
      />
    </div>
  );
};

export default WorkflowRegistryOrEdit;
