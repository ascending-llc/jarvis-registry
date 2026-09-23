import { useCallback, useEffect, useRef, useState } from 'react';
import { useGlobal } from '@/contexts/GlobalContext';
import SERVICES from '@/services';
import type { Tool } from '@/services/server/type';
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
  toggleTool: (mcpToolName: string) => void;
  handleSave: () => Promise<void>;
  requestClose: () => void;
}

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
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  const fetchData = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setLoaded(false);
    setTools([]);
    setDisabledTools(new Set());

    try {
      const result = await SERVICES.SERVER.getServerTools(serverId);
      if (requestId !== requestIdRef.current) return;

      setTools(toServerTools(result.toolFunctions));
      setDisabledTools(new Set(result.disabledTools || []));
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
  }, [serverId, showToast]);

  useEffect(() => {
    if (!isOpen) {
      requestIdRef.current += 1;
      if (wasOpenRef.current) {
        setLoading(false);
        setLoaded(false);
        setTools([]);
        setDisabledTools(new Set());
      }
      wasOpenRef.current = false;
      return;
    }
    wasOpenRef.current = true;
    void fetchData();
  }, [isOpen, fetchData]);

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
    if (!canManageTools || !loaded || loading || saving) return;

    setSaving(true);
    try {
      await SERVICES.SERVER.updateServerTools(serverId, { disabledTools: Array.from(disabledTools) });
      showToast?.('Tool settings updated successfully', 'success');
      onClose();
    } catch (error) {
      showToast?.(getErrorMessage(error, 'Failed to update tool settings'), 'error');
    } finally {
      setSaving(false);
    }
  }, [canManageTools, disabledTools, loaded, loading, onClose, saving, serverId, showToast]);

  return { tools, disabledTools, loading, loaded, saving, toggleTool, handleSave, requestClose };
};
