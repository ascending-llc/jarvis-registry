import { TrashIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { HiOutlineShare } from 'react-icons/hi2';
import { useNavigate, useSearchParams } from 'react-router-dom';

import AgentIcon from '@/assets/AgentIcon';
import CalendarIcon from '@/assets/CalendarIcon';
import ShareModal from '@/components/ShareModal';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import type { Agent } from '@/services/agent/type';
import MainConfigForm from './MainConfigForm';
import type { AgentConfig } from './types';

const INIT_DATA: AgentConfig = { title: '', description: '', path: '', url: '', type: '', trustAgent: false };

const STATUS_STYLE: Record<string, { pill: string; dot: string; label: string }> = {
  active: { pill: 'bg-[var(--jarvis-surface)] text-[var(--jarvis-success-text)]', dot: 'bg-[var(--jarvis-success)]', label: 'Active' },
  inactive: { pill: 'bg-[var(--jarvis-warning-soft)] text-[var(--jarvis-warning-text)]', dot: 'bg-[var(--jarvis-warning)]', label: 'Inactive' },
  error: { pill: 'bg-[var(--jarvis-surface)] text-[var(--jarvis-danger-text)]', dot: 'bg-[var(--jarvis-danger)]', label: 'Error' },
};

const getStatusStyle = (status?: string) =>
  STATUS_STYLE[status ?? ''] ?? { pill: 'bg-[var(--jarvis-surface)] text-[var(--jarvis-warning-text)]', dot: 'bg-[var(--jarvis-warning)]', label: 'Unknown' };

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object') {
    const err = error as {
      detail?: string | { message?: string; error?: string };
      message?: string;
      error?: string;
    };
    if (typeof err.detail === 'string') return err.detail;
    if (err.detail && typeof err.detail === 'object') {
      if (typeof err.detail.message === 'string') return err.detail.message;
      if (typeof err.detail.error === 'string') return err.detail.error;
    }
    if (typeof err.message === 'string') return err.message;
    if (typeof err.error === 'string') return err.error;
  }
  return fallback;
};

const AgentRegistryOrEdit: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const id = searchParams.get('id');
  const { showToast } = useGlobal();
  const { refreshAgentData, handleAgentUpdate } = useServer();

  const [loading, setLoading] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [agentDetail, setAgentDetail] = useState<Agent | null>(null);
  const [formData, setFormData] = useState<AgentConfig>(INIT_DATA);
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});

  const isEditMode = !!id;
  const isReadOnly = searchParams.get('isReadOnly') === 'true';

  const goBack = useCallback(() => {
    navigate(-1);
  }, [navigate]);

  const getDetail = useCallback(async () => {
    if (!id) return;
    setLoadingDetail(true);
    try {
      const result = await SERVICES.AGENT.getAgentDetail(id);
      const data: AgentConfig = {
        title: result.config?.title || result.name,
        description: result.config?.description || result.description,
        type: result.config?.type || '',
        path: result.path,
        url: result.config?.url || result.url || '',
        trustAgent: true,
      };
      setAgentDetail(result);
      setFormData(data);
    } catch (_error) {
      showToast('Failed to fetch agent details', 'error');
    } finally {
      setLoadingDetail(false);
    }
  }, [id, showToast]);

  useEffect(() => {
    if (id) getDetail();
  }, [id, getDetail]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        goBack();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [goBack]);

  const validate = () => {
    const newErrors: Record<string, string> = {};

    if (!formData.title.trim()) {
      newErrors.title = 'Title is required';
    }

    if (!formData.type) {
      newErrors.type = 'Transport Type is required';
    }

    if (!formData.path.trim()) {
      newErrors.path = 'Path is required';
    } else if (!/^\//.test(formData.path)) {
      newErrors.path = 'Path must start with /';
    } else if (!/^\/[a-zA-Z0-9\-._~%@!$&'()*+,;=:/]*$/.test(formData.path)) {
      newErrors.path = 'Path contains invalid characters';
    }

    if (!formData.url?.trim()) {
      newErrors.url = 'Agent URL is required';
    } else {
      try {
        const parsedUrl = new URL(formData.url);
        if (parsedUrl.protocol !== 'http:' && parsedUrl.protocol !== 'https:') {
          newErrors.url = 'URL must start with http:// or https://';
        }
      } catch (_) {
        newErrors.url = 'Invalid URL format';
      }
    }

    if (!formData.trustAgent) {
      newErrors.trustAgent = 'You must trust this agent';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const updateField = (field: keyof AgentConfig, value: any) => {
    setFormData(prev => ({ ...prev, [field]: value }));

    if (field === 'path') {
      const strVal = value as string | undefined;
      let pathError: string | undefined;
      if (strVal && !/^\//.test(strVal)) {
        pathError = 'Path must start with /';
      } else if (strVal && !/^\/[a-zA-Z0-9\-._~%@!$&'()*+,;=:/]*$/.test(strVal)) {
        pathError = 'Path contains invalid characters';
      }
      setErrors(prev => ({ ...prev, path: pathError }));
    } else if (errors[field as string]) {
      setErrors(prev => ({ ...prev, [field as string]: undefined }));
    }
  };

  const handleDelete = async () => {
    if (!id) return;
    try {
      await SERVICES.AGENT.deleteAgent(id);
      showToast('Agent deleted successfully', 'success');
      navigate('/', { replace: true });
      refreshAgentData(true);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to delete agent'), 'error');
    }
  };

  const handleSave = async () => {
    if (!validate()) return;

    setLoading(true);
    try {
      const payload = {
        title: formData.title,
        description: formData.description,
        path: formData.path,
        url: formData.url,
        type: formData.type,
      };

      if (isEditMode) {
        await SERVICES.AGENT.updateAgent(id, payload);
        showToast('Agent updated successfully', 'success');
        handleAgentUpdate(id, payload);
      } else {
        await SERVICES.AGENT.createAgent(payload);
        showToast('Agent created successfully', 'success');
        refreshAgentData(true);
      }
      goBack();
    } catch (error) {
      showToast(getErrorMessage(error, isEditMode ? 'Failed to update agent' : 'Failed to create agent'), 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {shareOpen && id && (
        <ShareModal
          itemName={formData.title || agentDetail?.name || 'Agent'}
          resourceId={id}
          resourceType='remoteAgent'
          isOpen={shareOpen}
          onClose={() => setShareOpen(false)}
        />
      )}
      <div className="h-full overflow-y-auto custom-scrollbar -mr-4 sm:-mr-6 lg:-mr-8">
        <div className="mx-auto flex flex-col w-3/4 min-h-full bg-[var(--jarvis-card)] rounded-lg">
        {/* Header */}
        <div className="px-6 py-6 flex items-center gap-4 border-b border-[color:var(--jarvis-border-soft)] border-[color:var(--jarvis-border)]">
          <div className="flex items-center justify-center p-3 rounded-xl bg-[#F3E8FF]">
            <AgentIcon className="h-8 w-8 text-[var(--jarvis-primary)]" />
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-[var(--jarvis-text-strong)] m-0">
                {isReadOnly ? 'View Agent' : isEditMode ? 'Edit Agent' : 'Register Agent'}
              </h1>
              {isReadOnly &&
                agentDetail &&
                (() => {
                  const { pill, dot, label } = getStatusStyle(agentDetail.status);
                  return (
                    <span
                      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium ${pill}`}
                    >
                      <span className={`w-2 h-2 rounded-full inline-block ${dot}`} />
                      {label}
                    </span>
                  );
                })()}
            </div>
            <p className="text-base text-[var(--jarvis-muted)] mt-0.5">Configure an Agent</p>
          </div>
        </div>
        {/* Content */}
        <div className="px-6 py-6 flex-1 flex flex-col">
          {loadingDetail ? (
            <div className="flex-1 flex items-center justify-center min-h-[200px]">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--jarvis-primary)]"></div>
            </div>
          ) : (
            <div className="space-y-8">
              {isEditMode && agentDetail && (
                <div className="p-4 bg-[var(--jarvis-bg)] bg-[var(--jarvis-card)]/50 rounded-lg border border-[color:var(--jarvis-border)] w-full">
                  <span className="block text-xs font-medium text-[var(--jarvis-muted)] mb-1 uppercase tracking-wide">
                    Created At
                  </span>
                  <div className="text-sm text-[var(--jarvis-text)] flex items-center gap-1.5">
                    <CalendarIcon className="h-4 w-4 text-[var(--jarvis-muted)] shrink-0" />
                    {new Date(agentDetail.createdAt || new Date()).toLocaleString(undefined, {
                      dateStyle: 'medium',
                      timeStyle: 'short',
                    })}
                  </div>
                </div>
              )}
              <MainConfigForm
                formData={formData}
                agentDetail={agentDetail}
                updateField={updateField}
                errors={errors}
                isReadOnly={isReadOnly}
              />
            </div>
          )}
        </div>
        {/* Footer */}
        <div className="px-6 py-4 border-t border-[color:var(--jarvis-border-soft)] border-[color:var(--jarvis-border)] flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            {isEditMode && !isReadOnly && (
              <button
                onClick={handleDelete}
                disabled={loading}
                className="inline-flex items-center px-4 py-2 border border-[color:var(--jarvis-border)] rounded-md shadow-sm text-sm font-medium text-[var(--jarvis-danger-text)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-danger-soft)] hover:bg-[var(--jarvis-danger-soft)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--jarvis-danger)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <TrashIcon className="h-4 w-4" />
              </button>
            )}
            {isEditMode && !!id && agentDetail?.permissions?.SHARE && (
              <button
                onClick={() => setShareOpen(true)}
                disabled={loading || loadingDetail}
                className="inline-flex items-center px-4 py-2 border border-[color:var(--jarvis-border)] rounded-md shadow-sm text-sm font-medium text-[var(--jarvis-primary)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-primary-soft)] hover:bg-[var(--jarvis-primary-soft)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--jarvis-primary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <HiOutlineShare className="h-4 w-4" />
              </button>
            )}
          </div>
          <div className="flex gap-3">
            {isReadOnly ? (
              <button
                onClick={goBack}
                disabled={loading}
                className="min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-[color:var(--jarvis-border)] rounded-md shadow-sm text-sm font-medium text-[var(--jarvis-text)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-card-muted)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--jarvis-primary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                Back
              </button>
            ) : (
              <>
                <button
                  onClick={goBack}
                  disabled={loading}
                  className="min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-[color:var(--jarvis-border)] rounded-md shadow-sm text-sm font-medium text-[var(--jarvis-text)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-card-muted)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--jarvis-primary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={loading}
                  className="inline-flex items-center justify-center gap-2 min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-[var(--jarvis-primary-hover)] hover:bg-[var(--jarvis-primary-hover)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--jarvis-primary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {loading && <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />}
                  {isEditMode ? 'Save Changes' : 'Register Agent'}
                </button>
              </>
            )}
          </div>
        </div>
        </div>
      </div>
    </>
  );
};

export default AgentRegistryOrEdit;
