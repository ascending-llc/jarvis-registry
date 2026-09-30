import { useCallback, useEffect, useMemo, useState } from 'react';
import type { WorkflowSchedule } from '@/services/workflow/type';
import SERVICES from '@/services';
import {
  createDefaultScheduleDraft,
  getScheduleDraftKey,
  getScheduleSummary,
  scheduleDraftToRequest,
  scheduleToDraft,
  validateScheduleDraft,
  type WorkflowScheduleController,
  type WorkflowScheduleDraft,
} from '@/components/WorkflowCanvas/PropsPanel/Schedule/model';

interface UseWorkflowScheduleOptions {
  workflowId?: string;
  workflowEnabled: boolean;
  workflowCanEdit: boolean;
  isReadOnly: boolean;
  pageBusy: boolean;
  hasUnsavedWorkflowChanges: () => boolean;
  onError: (message: string) => void;
}

const _getErrorMessage = (error: unknown, fallback: string): string => {
  if (!error || typeof error !== 'object' || !('detail' in error)) return fallback;
  const detail = error.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
    return detail.message;
  }
  return fallback;
};

export const useWorkflowSchedule = ({
  workflowId,
  workflowEnabled,
  workflowCanEdit,
  isReadOnly,
  pageBusy,
  hasUnsavedWorkflowChanges,
  onError,
}: UseWorkflowScheduleOptions): WorkflowScheduleController => {
  const [schedule, setSchedule] = useState<WorkflowSchedule | null>(null);
  const [draft, setDraft] = useState<WorkflowScheduleDraft>(createDefaultScheduleDraft);
  const [baselineKey, setBaselineKey] = useState(() => getScheduleDraftKey(createDefaultScheduleDraft()));
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    let active = true;
    const defaultDraft = createDefaultScheduleDraft();

    if (!workflowId) {
      setSchedule(null);
      setDraft(defaultDraft);
      setBaselineKey(getScheduleDraftKey(defaultDraft));
      setLoading(false);
      setLoadError(null);
      return () => {
        active = false;
      };
    }

    setLoading(true);
    setLoadError(null);
    void SERVICES.WORKFLOW.getWorkflowSchedules(workflowId)
      .then(response => {
        if (!active) return;
        const currentSchedule = response.items[0] ?? null;
        const nextDraft = currentSchedule ? scheduleToDraft(currentSchedule) : defaultDraft;
        setSchedule(currentSchedule);
        setDraft(nextDraft);
        setBaselineKey(getScheduleDraftKey(nextDraft));
      })
      .catch((error: unknown) => {
        if (!active) return;
        setLoadError(_getErrorMessage(error, 'Failed to load workflow schedule'));
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [retryKey, workflowId]);

  const isDirty = useMemo(() => getScheduleDraftKey(draft) !== baselineKey, [baselineKey, draft]);
  const validationErrors = useMemo(() => validateScheduleDraft(draft), [draft]);
  const hasEditPermission = schedule ? schedule.permissions.EDIT : workflowCanEdit;
  const canEdit = Boolean(workflowId) && !isReadOnly && hasEditPermission && !loading && !loadError;
  const canToggle = canEdit && !pageBusy && !saving && !toggling;

  const setDraftPatch = useCallback((patch: Partial<WorkflowScheduleDraft>) => {
    setDraft(current => ({ ...current, ...patch }));
  }, []);

  const persistDraft = useCallback(
    async (forceCreate: boolean): Promise<WorkflowSchedule | null> => {
      if (!workflowId || !canEdit) return null;
      if (!workflowEnabled && !schedule) {
        onError('Enable the workflow before creating its schedule');
        return null;
      }
      if (schedule && !isDirty) return schedule;
      if (!schedule && !forceCreate && !isDirty) return null;

      const request = scheduleDraftToRequest(draft);
      if (!request) {
        onError('Fix the schedule fields before saving');
        return null;
      }

      setSaving(true);
      try {
        const savedSchedule = schedule
          ? await SERVICES.WORKFLOW.updateWorkflowSchedule(workflowId, schedule.id, request)
          : await SERVICES.WORKFLOW.createWorkflowSchedule(workflowId, request);
        const savedDraft = scheduleToDraft(savedSchedule);
        setSchedule(savedSchedule);
        setDraft(savedDraft);
        setBaselineKey(getScheduleDraftKey(savedDraft));
        return savedSchedule;
      } catch (error: unknown) {
        onError(_getErrorMessage(error, 'Failed to save workflow schedule'));
        return null;
      } finally {
        setSaving(false);
      }
    },
    [canEdit, draft, isDirty, onError, schedule, workflowEnabled, workflowId],
  );

  const saveDraft = useCallback(async (): Promise<boolean> => {
    if (!isDirty) return true;
    return (await persistDraft(false)) !== null;
  }, [isDirty, persistDraft]);

  const toggleEnabled = useCallback(
    async (enabled: boolean): Promise<boolean> => {
      if (!workflowId || !canToggle) return false;
      if (schedule?.enabled === enabled) return true;
      if (!enabled && !schedule) return true;

      if (enabled && hasUnsavedWorkflowChanges()) {
        onError('Update the workflow before enabling its schedule');
        return false;
      }
      if (enabled && !workflowEnabled) {
        onError('Enable the workflow before enabling its schedule');
        return false;
      }

      const targetSchedule = enabled && (!schedule || isDirty) ? await persistDraft(true) : schedule;
      if (!targetSchedule) return false;

      setToggling(true);
      try {
        const updatedSchedule = await SERVICES.WORKFLOW.toggleWorkflowSchedule(workflowId, targetSchedule.id, {
          enabled,
        });
        setSchedule(updatedSchedule);
        return true;
      } catch (error: unknown) {
        onError(_getErrorMessage(error, `Failed to ${enabled ? 'enable' : 'disable'} workflow schedule`));
        return false;
      } finally {
        setToggling(false);
      }
    },
    [canToggle, hasUnsavedWorkflowChanges, isDirty, onError, persistDraft, schedule, workflowEnabled, workflowId],
  );

  return {
    schedule,
    draft,
    loading,
    saving,
    toggling,
    pageBusy,
    loadError,
    isDirty,
    isNewWorkflow: !workflowId,
    parentEnabled: workflowEnabled,
    canEdit,
    canToggle,
    activeSummary: schedule?.enabled ? getScheduleSummary(schedule.cronExpression, schedule.timezone) : null,
    validationErrors,
    setDraftPatch,
    retry: () => setRetryKey(current => current + 1),
    saveDraft,
    toggleEnabled,
  };
};
