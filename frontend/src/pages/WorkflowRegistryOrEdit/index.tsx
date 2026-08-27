import { CheckIcon, CogIcon, PlayIcon } from '@heroicons/react/24/outline';
import type { Edge } from '@xyflow/react';
import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { HiOutlineShare } from 'react-icons/hi2';
import { useBlocker, useNavigate, useSearchParams } from 'react-router-dom';
import ShareModal from '@/components/ShareModal';
import WorkflowCanvas from '@/components/WorkflowCanvas';
import {
  apiNodesToCanvas,
  canvasToApiNodes,
  validateApiNodes,
  validateCanvasNodes,
} from '@/components/WorkflowCanvas/convert';
import type { WorkflowNode as CanvasWorkflowNode, WorkflowCanvasRef } from '@/components/WorkflowCanvas/types';
import { useAuth } from '@/contexts/AuthContext';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import type {
  PendingAuthorization,
  TriggerWorkflowRunRequest,
  WorkflowNode as ApiWorkflowNode,
  Workflow,
} from '@/services/workflow/type';
import DeleteWorkflowDialog from './DeleteWorkflowDialog';
import { useActiveWorkflowRun } from './hooks/useActiveWorkflowRun';
import { useWorkflowDraftGuard } from './hooks/useWorkflowDraftGuard';
import TriggerRunModal from './TriggerRunModal';
import UnsavedChangesDialog from './UnsavedChangesDialog';
import WorkflowReauthModal from './WorkflowReauthModal';

type MutatingAction = 'idle' | 'saving' | 'triggering' | 'deleting';
type WorkflowTriggerInput = NonNullable<TriggerWorkflowRunRequest['initialInput']>;

interface PendingWorkflowReauth {
  initialInput: WorkflowTriggerInput;
  authorizations: PendingAuthorization[];
}

const _getDetailErrorMessage = (error: unknown): string => {
  if (!error || typeof error !== 'object' || !('detail' in error)) return 'Failed to fetch workflow';
  const detail = error.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
    return detail.message;
  }
  return 'Failed to fetch workflow';
};

const WorkflowRegistryOrEdit: React.FC = () => {
  // ── 1. Context & Routing ─────────────────────────────────────────────────────────
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const { showToast } = useGlobal();
  const { refreshWorkflowData, handleWorkflowUpdate } = useServer();

  const id = searchParams.get('id');
  const isReadOnly = searchParams.get('isReadOnly') === 'true';
  const isEditMode = !!id;
  const canControlWorkflow = user?.scopes?.includes('workflows-control') === true;
  const canvasRef = useRef<WorkflowCanvasRef>(null);
  const triggeringRef = useRef(false);
  const detailRequestGenerationRef = useRef(0);

  // ── 2. Resource State ────────────────────────────────────────────────────────────
  const [workflow, setWorkflow] = useState<Partial<Workflow> | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailLoadError, setDetailLoadError] = useState<string | null>(null);
  const isExistingDetailReady = !isEditMode || workflow?.id === id;
  const currentWorkflow = workflow?.id === id || (!isEditMode && workflow?.id === undefined) ? workflow : null;
  const existingDetailUnavailable = isEditMode && (loadingDetail || !isExistingDetailReady || !!detailLoadError);
  const canTriggerWorkflow = isEditMode && canControlWorkflow;

  // ── 3. Mutating Action (State Machine) ─────────────────────────────────────────
  const [mutatingAction, setMutatingAction] = useState<MutatingAction>('idle');

  // ── 4. Dirty Checking & UI State ───────────────────────────────────────────────
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [triggerModalOpen, setTriggerModalOpen] = useState(false);
  const [reauthModalOpen, setReauthModalOpen] = useState(false);
  const [pendingWorkflowReauth, setPendingWorkflowReauth] = useState<PendingWorkflowReauth | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [runHistoryRefresh, setRunHistoryRefresh] = useState(0);
  const canShareWorkflow = isEditMode && isExistingDetailReady && workflow?.permissions?.SHARE === true;
  const activeWorkflowRun = useActiveWorkflowRun(id ?? undefined, message => showToast(message, 'error'));

  useEffect(() => {
    if (activeWorkflowRun.isLocked) setTriggerModalOpen(false);
  }, [activeWorkflowRun.isLocked]);

  // ── Side Effects: Save shortcut (Cmd+S / Ctrl+S) ───────────────────────────────
  useEffect(() => {
    if (isReadOnly || mutatingAction !== 'idle' || existingDetailUnavailable) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        canvasRef.current?.save();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [existingDetailUnavailable, isReadOnly, mutatingAction]);

  // ── Fetch Initial Data ─────────────────────────────────────────────────────────
  useEffect(() => {
    const requestGeneration = ++detailRequestGenerationRef.current;

    if (id) {
      setWorkflow(null);
      setDetailLoadError(null);
      setLoadingDetail(true);
      getDetail(id, requestGeneration);
    } else {
      setLoadingDetail(false);
      setDetailLoadError(null);
      setWorkflow({
        name: searchParams.get('name') ?? 'New Workflow',
        description: '',
      });
    }

    return () => {
      if (detailRequestGenerationRef.current === requestGeneration) {
        detailRequestGenerationRef.current += 1;
      }
    };
  }, [id, searchParams]);

  const getDetail = async (workflowId: string, requestGeneration: number) => {
    try {
      const data = await SERVICES.WORKFLOW.getWorkflowDetail(workflowId);
      if (detailRequestGenerationRef.current !== requestGeneration) return;
      setDetailLoadError(null);
      setWorkflow(data);
    } catch (error: unknown) {
      if (detailRequestGenerationRef.current !== requestGeneration) return;
      const message = _getDetailErrorMessage(error);
      setDetailLoadError(message);
      showToast(message, 'error');
    } finally {
      if (detailRequestGenerationRef.current === requestGeneration) setLoadingDetail(false);
    }
  };

  // ── Derive initial canvas elements from loaded workflow ────────────────────
  const initialCanvas = useMemo(() => {
    if (!isEditMode || !workflow) return { nodes: undefined, edges: undefined, error: null };
    try {
      const converted = apiNodesToCanvas(workflow.nodes ?? []);
      return { ...converted, error: null };
    } catch (error) {
      return {
        nodes: undefined,
        edges: undefined,
        error: error instanceof Error ? error.message : 'Failed to load workflow graph',
      };
    }
  }, [isEditMode, workflow]);

  const { discardChanges, isDirty, markSaved } = useWorkflowDraftGuard({
    canvasRef,
    workflow,
    resourceKey: id ?? undefined,
    isReadOnly,
    initialNodes: initialCanvas.nodes,
    initialEdges: initialCanvas.edges,
  });

  // ── Side Effects: Block navigation & BeforeUnload ──────────────────────────────
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    if (isReadOnly) return false;
    const currentUrl = currentLocation.pathname + currentLocation.search;
    const nextUrl = nextLocation.pathname + nextLocation.search;
    return isDirty() && currentUrl !== nextUrl;
  });

  useEffect(() => {
    if (isReadOnly) return;
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isDirty()) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [isDirty, isReadOnly]);

  useEffect(() => {
    if (initialCanvas.error) showToast(initialCanvas.error, 'error');
  }, [initialCanvas.error, showToast]);

  // ── Actions: Save ────────────────────────────────────────────────────────────
  const handleSave = async (
    nodes: CanvasWorkflowNode[],
    edges: Edge[],
    viewport: { x: number; y: number; zoom: number },
  ) => {
    if (isReadOnly) return;
    if (existingDetailUnavailable) {
      showToast(detailLoadError ?? 'Workflow details are not ready', 'error');
      return;
    }
    const canvasValidationError = validateCanvasNodes(nodes, edges);
    if (canvasValidationError) {
      showToast(canvasValidationError, 'error');
      return;
    }

    let apiNodes: ReturnType<typeof canvasToApiNodes>;
    try {
      apiNodes = canvasToApiNodes(nodes, edges);
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Failed to convert workflow', 'error');
      return;
    }

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
    const validatedNodes = apiNodes as ApiWorkflowNode[];

    setMutatingAction('saving');

    try {
      if (isEditMode && id) {
        const submittedMetadata = {
          name: workflow?.name,
          description: workflow?.description,
        };
        const updated = await SERVICES.WORKFLOW.updateWorkflow(id, {
          ...submittedMetadata,
          nodes: validatedNodes,
          canvas: { viewport },
        });
        handleWorkflowUpdate(id, { nodeCount: updated.numNodes ?? validatedNodes.length, name: workflow?.name });
        markSaved(submittedMetadata, nodes, edges, workflow);
        setWorkflow(current => (current === workflow && current ? { ...current, ...submittedMetadata } : current));
        showToast('Workflow updated successfully!', 'success');
      } else {
        const submittedMetadata = {
          name: workflow?.name?.trim() || 'New Workflow',
          description: workflow?.description?.trim() || undefined,
        };
        await SERVICES.WORKFLOW.createWorkflow({
          ...submittedMetadata,
          nodes: validatedNodes,
          canvas: { viewport },
        });
        markSaved(submittedMetadata, nodes, edges, workflow);
        setWorkflow(current => (current === workflow && current ? { ...current, ...submittedMetadata } : current));
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
  const handleTrigger = async (initialInput: WorkflowTriggerInput = {}) => {
    if (!canControlWorkflow) {
      setTriggerModalOpen(false);
      showToast('You do not have permission to control workflows', 'error');
      return;
    }
    if (!id) {
      showToast('Save the workflow before triggering a run', 'error');
      return;
    }
    if (existingDetailUnavailable) {
      setTriggerModalOpen(false);
      showToast(detailLoadError ?? 'Workflow details are not ready', 'error');
      return;
    }
    if (activeWorkflowRun.isLocked || triggeringRef.current) {
      setTriggerModalOpen(false);
      showToast('A workflow run is already active or starting', 'error');
      return;
    }

    triggeringRef.current = true;
    setTriggerModalOpen(false);
    setMutatingAction('triggering');
    try {
      const response = await SERVICES.WORKFLOW.triggerWorkflowRun(id, { initialInput });
      if (response.requiresReauth) {
        setPendingWorkflowReauth({
          initialInput,
          authorizations: response.pendingAuthorizations,
        });
        setReauthModalOpen(true);
        return;
      }

      activeWorkflowRun.trackRun(response.runId);
      setRunHistoryRefresh(k => k + 1);
      showToast('Workflow run triggered!', 'success');
    } catch (error: any) {
      const msg = error?.detail?.message || (typeof error?.detail === 'string' ? error.detail : '');
      showToast(msg || 'Failed to trigger workflow run', 'error');
    } finally {
      triggeringRef.current = false;
      setMutatingAction('idle');
    }
  };

  const handleRetryAfterReauth = () => {
    if (!pendingWorkflowReauth) return;
    const { initialInput } = pendingWorkflowReauth;
    setReauthModalOpen(false);
    void handleTrigger(initialInput);
  };

  // ── Actions: Workflow metadata change (from PropsPanel) ──────────────────────
  const handleWorkflowChange = (patch: Partial<Pick<Workflow, 'name' | 'description'>>) => {
    if (isReadOnly) return;
    setWorkflow(prev => (prev ? { ...prev, ...patch } : prev));
  };

  // ── Actions: Delete workflow ─────────────────────────────────────────────────
  const handleDeleteWorkflow = async () => {
    if (isReadOnly) return;
    if (!id) return;

    setMutatingAction('deleting');
    try {
      await SERVICES.WORKFLOW.deleteWorkflow(id);
      await refreshWorkflowData();
      showToast('Workflow deleted', 'success');
      discardChanges();
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
            {currentWorkflow?.name ?? (isEditMode ? 'Workflow' : 'New Workflow')}
          </span>

          {/* Settings button */}
          <button
            type='button'
            onClick={() => canvasRef.current?.togglePanel()}
            disabled={existingDetailUnavailable}
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

        {canShareWorkflow || canTriggerWorkflow || !isReadOnly ? (
          <div className='flex items-center gap-2 flex-shrink-0'>
            {canShareWorkflow && (
              <button
                type='button'
                onClick={() => setShareOpen(true)}
                disabled={existingDetailUnavailable}
                title='Share workflow'
                aria-label='Share workflow'
                className='inline-flex items-center justify-center rounded-md border border-transparent bg-[var(--jarvis-primary-soft)] p-1.5 text-[var(--jarvis-primary-text)] hover:bg-[var(--jarvis-primary)]/20 focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50'
              >
                <HiOutlineShare className='h-4 w-4' />
              </button>
            )}

            {canTriggerWorkflow && (
              <button
                onClick={() => setTriggerModalOpen(true)}
                disabled={mutatingAction !== 'idle' || activeWorkflowRun.isLocked || existingDetailUnavailable}
                className='inline-flex items-center gap-1 px-2.5 py-1 border border-transparent rounded-md text-xs font-medium text-white bg-[var(--jarvis-primary)] hover:opacity-90 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed'
              >
                {mutatingAction === 'triggering' ? (
                  <span className='h-3.5 w-3.5 animate-spin rounded-full border-b-2 border-white' />
                ) : (
                  <PlayIcon className='h-3.5 w-3.5' />
                )}
                Trigger run
              </button>
            )}

            {!isReadOnly && (
              <button
                onClick={() => canvasRef.current?.save()}
                disabled={mutatingAction !== 'idle' || existingDetailUnavailable}
                className='inline-flex items-center justify-center gap-1 px-2.5 py-1 border border-transparent rounded-md text-xs font-medium text-white bg-[var(--jarvis-primary-hover)] hover:opacity-90 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed'
              >
                {mutatingAction === 'saving' ? (
                  <span className='h-3.5 w-3.5 animate-spin rounded-full border-b-2 border-white' />
                ) : (
                  <CheckIcon className='h-3.5 w-3.5' />
                )}
                {isEditMode ? 'Update' : 'Save'}
              </button>
            )}
          </div>
        ) : null}
      </div>

      {/* ── Canvas ──────────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        {isEditMode && detailLoadError ? (
          <div className='flex h-full items-center justify-center p-8'>
            <div className='max-w-lg rounded-lg border border-[var(--jarvis-danger)] bg-[var(--jarvis-danger-soft)] p-4 text-sm text-[var(--jarvis-danger-text)]'>
              <div>Unable to load workflow: {detailLoadError}</div>
              <div className='mt-2'>Reload the page to try again.</div>
            </div>
          </div>
        ) : isEditMode && (loadingDetail || !isExistingDetailReady) ? (
          <div className='flex h-full items-center justify-center'>
            <div className='h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-primary)]' />
          </div>
        ) : initialCanvas.error ? (
          <div className='flex h-full items-center justify-center p-8'>
            <div className='max-w-lg rounded-lg border border-[var(--jarvis-danger)] bg-[var(--jarvis-danger-soft)] p-4 text-sm text-[var(--jarvis-danger-text)]'>
              Unable to load workflow graph: {initialCanvas.error}
            </div>
          </div>
        ) : (
          // key forces canvas remount when switching between workflows
          <WorkflowCanvas
            key={id ?? 'new'}
            ref={canvasRef}
            workflowId={id ?? undefined}
            workflow={currentWorkflow}
            refreshRunHistoryKey={runHistoryRefresh}
            activeWorkflowRun={activeWorkflowRun.activeRun}
            isMonitoringActive={activeWorkflowRun.isMonitoringActive}
            refetchActiveWorkflowRun={activeWorkflowRun.refetchNow}
            initialNodes={initialCanvas.nodes}
            initialEdges={initialCanvas.edges}
            isReadOnly={isReadOnly}
            isNewWorkflow={!isEditMode}
            onDeleteWorkflow={() => {
              if (!isReadOnly) setDeleteDialogOpen(true);
            }}
            onWorkflowChange={handleWorkflowChange}
            onSave={handleSave}
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
        workflowName={currentWorkflow?.name ?? 'New Workflow'}
        deleting={mutatingAction === 'deleting'}
        onCancel={() => setDeleteDialogOpen(false)}
        onConfirm={handleDeleteWorkflow}
      />

      {/* ── Trigger run modal ─────────────────────────────────────────────────── */}
      <TriggerRunModal
        isOpen={triggerModalOpen}
        workflowName={currentWorkflow?.name ?? ''}
        onClose={() => setTriggerModalOpen(false)}
        onTrigger={handleTrigger}
        triggering={mutatingAction === 'triggering'}
      />

      <WorkflowReauthModal
        isOpen={reauthModalOpen}
        workflowName={currentWorkflow?.name ?? 'Workflow'}
        pendingAuthorizations={pendingWorkflowReauth?.authorizations ?? []}
        onClose={() => setReauthModalOpen(false)}
        onRetryRun={handleRetryAfterReauth}
        retrying={mutatingAction === 'triggering'}
      />

      {shareOpen && id && isExistingDetailReady && (
        <ShareModal
          itemName={currentWorkflow?.name ?? 'Workflow'}
          resourceId={id}
          resourceType='workflow'
          isOpen={shareOpen}
          onClose={() => setShareOpen(false)}
        />
      )}
    </div>
  );
};

export default WorkflowRegistryOrEdit;
