import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useGlobal } from '@/contexts/GlobalContext';
import SERVICES from '@/services';
import type { GetServerToolsResponse, Tool } from '@/services/server/type';
import { getErrorMessage } from '@/utils/getErrorMessage';

export interface ServerToolsModalProps {
  isOpen: boolean;
  onClose: () => void;
  serverId: string;
  serverName: string;
  canManageTools: boolean;
}

/** A tool whose `mcpToolName` is always set: the identity used by `disabledTools`. */
export type ServerTool = Tool & { mcpToolName: string };

interface ServerToolsModalState {
  tools: ServerTool[];
  disabledTools: ReadonlySet<string>;
  loading: boolean;
  loaded: boolean;
  saving: boolean;
  /** Whether the ticks differ from the last loaded or saved `disabledTools`. */
  hasChanges: boolean;
  toggleTool: (mcpToolName: string) => void;
  handleSave: () => Promise<void>;
  requestClose: () => void;
}

const sameMembers = (a: ReadonlySet<string>, b: ReadonlySet<string>): boolean =>
  a.size === b.size && [...a].every(name => b.has(name));

/** Mirror the backend's `td.get("mcpToolName", key)` so ids match what `disabledTools` stores. */
export const toServerTools = (toolFunctions: Record<string, Tool> | undefined): ServerTool[] =>
  Object.entries(toolFunctions ?? {}).map(([key, tool]) => ({ ...tool, mcpToolName: tool.mcpToolName || key }));

export const useServerToolsModal = ({
  isOpen,
  onClose,
  serverId,
  canManageTools,
}: ServerToolsModalProps): ServerToolsModalState => {
  const { showToast } = useGlobal();
  const requestIdRef = useRef(0);
  const wasOpenRef = useRef(false);
  const [tools, setTools] = useState<ServerTool[]>([]);
  const [disabledTools, setDisabledTools] = useState<Set<string>>(() => new Set());
  const [savedDisabledTools, setSavedDisabledTools] = useState<ReadonlySet<string>>(() => new Set());
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  const hasChanges = useMemo(
    () => !sameMembers(disabledTools, savedDisabledTools),
    [disabledTools, savedDisabledTools],
  );

  const applyServerState = useCallback((result: GetServerToolsResponse) => {
    const saved = result.disabledTools || [];
    setTools(toServerTools(result.toolFunctions));
    setDisabledTools(new Set(saved));
    setSavedDisabledTools(new Set(saved));
  }, []);

  const resetState = useCallback(() => {
    setTools([]);
    setDisabledTools(new Set());
    setSavedDisabledTools(new Set());
  }, []);

  const fetchData = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setLoaded(false);
    resetState();

    try {
      const result = await SERVICES.SERVER.getServerTools(serverId);
      if (requestId !== requestIdRef.current) return;

      applyServerState(result);
      setLoaded(true);
    } catch {
      if (requestId === requestIdRef.current) {
        showToast?.('Failed to fetch tools', 'error');
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  }, [applyServerState, resetState, serverId, showToast]);

  useEffect(() => {
    if (!isOpen) {
      requestIdRef.current += 1;
      if (wasOpenRef.current) {
        setLoading(false);
        setLoaded(false);
        resetState();
      }
      wasOpenRef.current = false;
      return;
    }
    wasOpenRef.current = true;
    void fetchData();
  }, [isOpen, fetchData, resetState]);

  const toggleTool = useCallback(
    (mcpToolName: string) => {
      if (!canManageTools || !loaded || saving) return;

      setDisabledTools(previous => {
        const next = new Set(previous);
        if (next.has(mcpToolName)) next.delete(mcpToolName);
        else next.add(mcpToolName);
        return next;
      });
    },
    [canManageTools, loaded, saving],
  );

  // Escape, backdrop clicks and the close buttons all route here; a save in flight must finish first.
  const requestClose = useCallback(() => {
    if (saving) return;
    onClose();
  }, [onClose, saving]);

  const handleSave = useCallback(async () => {
    if (!canManageTools || !loaded || loading || saving || !hasChanges) return;

    // Closing the modal or switching servers bumps requestIdRef; a late response must not repopulate it.
    const requestId = requestIdRef.current;
    setSaving(true);
    try {
      const result = await SERVICES.SERVER.updateServerTools(serverId, { disabledTools: Array.from(disabledTools) });
      showToast?.('Tool settings updated successfully', 'success');
      // Show what the server stored (it drops names that are not current tools), not what was sent.
      if (requestId === requestIdRef.current) applyServerState(result);
    } catch (error) {
      showToast?.(getErrorMessage(error, 'Failed to update tool settings'), 'error');
    } finally {
      setSaving(false);
    }
  }, [applyServerState, canManageTools, disabledTools, hasChanges, loaded, loading, saving, serverId, showToast]);

  return { tools, disabledTools, loading, loaded, saving, hasChanges, toggleTool, handleSave, requestClose };
};
