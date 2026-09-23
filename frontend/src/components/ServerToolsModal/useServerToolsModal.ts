import { useCallback, useEffect, useRef, useState } from 'react';
import { useGlobal } from '@/contexts/GlobalContext';
import SERVICES from '@/services';
import type { Tool } from '@/services/server/type';

export interface ServerToolsModalProps {
  isOpen: boolean;
  onClose: () => void;
  serverId: string;
  serverName: string;
  canManageTools: boolean;
}

interface ServerToolsModalState {
  tools: Tool[];
  disabledTools: ReadonlySet<string>;
  loading: boolean;
  loaded: boolean;
  saving: boolean;
  toggleTool: (mcpToolName: string) => void;
  handleSave: () => Promise<void>;
}

const getErrorMessage = (error: unknown): string => {
  if (!error || typeof error !== 'object' || !('detail' in error)) {
    return 'Failed to update tool settings';
  }

  const detail = error.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
    return detail.message;
  }
  return 'Failed to update tool settings';
};

export const useServerToolsModal = ({
  isOpen,
  onClose,
  serverId,
  canManageTools,
}: ServerToolsModalProps): ServerToolsModalState => {
  const { showToast } = useGlobal();
  const requestIdRef = useRef(0);
  const wasOpenRef = useRef(false);
  const [tools, setTools] = useState<Tool[]>([]);
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

      setTools(Object.values(result.toolFunctions || {}));
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

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const handleSave = useCallback(async () => {
    if (!canManageTools || !loaded || loading || saving) return;

    setSaving(true);
    try {
      await SERVICES.SERVER.updateServerTools(serverId, { disabledTools: Array.from(disabledTools) });
      showToast?.('Tool settings updated successfully', 'success');
      onClose();
    } catch (error) {
      showToast?.(getErrorMessage(error), 'error');
    } finally {
      setSaving(false);
    }
  }, [canManageTools, disabledTools, loaded, loading, onClose, saving, serverId, showToast]);

  return { tools, disabledTools, loading, loaded, saving, toggleTool, handleSave };
};
