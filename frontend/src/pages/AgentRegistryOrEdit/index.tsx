import { TrashIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import AgentIcon from '@/assets/AgentIcon';
import CalendarIcon from '@/assets/CalendarIcon';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import type { Agent } from '@/services/agent/type';
import MainConfigForm from './MainConfigForm';
import type { AgentConfig } from './types';

const INIT_DATA: AgentConfig = { title: '', description: '', path: '', url: '', trustAgent: false };

const STATUS_STYLE: Record<string, { pill: string; dot: string; label: string }> = {
  active: { pill: 'bg-emerald-500/15 text-emerald-300', dot: 'bg-emerald-400', label: 'Active' },
  inactive: { pill: 'bg-orange-500/15 text-orange-300', dot: 'bg-orange-400', label: 'Inactive' },
  error: { pill: 'bg-red-500/15 text-red-300', dot: 'bg-red-400', label: 'Error' },
};

const getStatusStyle = (status?: string) =>
  STATUS_STYLE[status ?? ''] ?? { pill: 'bg-amber-500/15 text-amber-300', dot: 'bg-amber-400', label: 'Unknown' };

const AgentRegistryOrEdit: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const id = searchParams.get('id');
  const { showToast } = useGlobal();
  const { refreshAgentData, handleAgentUpdate } = useServer();

  const [loading, setLoading] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
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
        title: result.name,
        description: result.description,
        path: result.path,
        url: result.url || '',
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

    if (!formData.path.trim()) {
      newErrors.path = 'Path is required';
    } else if (!/^\//.test(formData.path)) {
      newErrors.path = 'Path must start with /';
    }

    if (!formData.url?.trim()) {
      newErrors.url = 'Agent URL is required';
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
      const isInvalid = value && !/^\//.test(value as string);
      setErrors(prev => ({ ...prev, path: isInvalid ? 'Path must start with /' : undefined }));
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
    } catch (error: any) {
      showToast(error?.detail || error, 'error');
    }
  };

  const handleSave = async () => {
    if (!validate()) return;

    setLoading(true);
    try {
      const payload = {
        name: formData.title,
        description: formData.description,
        path: formData.path,
        url: formData.url,
        version: '1.0.0',
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
    } catch (error: any) {
      showToast(error?.detail || error, 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className='h-full overflow-y-auto custom-scrollbar -mr-4 sm:-mr-6 lg:-mr-8'>
      <div className='mx-auto flex flex-col w-3/4 min-h-full bg-white dark:bg-gray-800 rounded-lg'>
        {/* Header */}
        <div className='px-6 py-6 flex items-center gap-4 border-b border-gray-100 dark:border-gray-700'>
          <div className='flex items-center justify-center p-3 rounded-xl bg-[#F3E8FF] dark:bg-purple-900/30'>
            <AgentIcon className='h-8 w-8 text-purple-600 dark:text-purple-300' />
          </div>
          <div className='flex-1'>
            <div className='flex items-center gap-3'>
              <h1 className='text-2xl font-bold text-gray-900 dark:text-white m-0'>
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
            <p className='text-base text-gray-500 dark:text-gray-400 mt-0.5'>Configure an Agent</p>
          </div>
        </div>
        {/* Content */}
        <div className='px-6 py-6 flex-1 flex flex-col'>
          {loadingDetail ? (
            <div className='flex-1 flex items-center justify-center min-h-[200px]'>
              <div className='animate-spin rounded-full h-8 w-8 border-b-2 border-purple-600'></div>
            </div>
          ) : (
            <div className='space-y-8'>
              {isEditMode && agentDetail && (
                <div className='p-4 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-gray-200 dark:border-gray-700 w-full'>
                  <span className='block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1 uppercase tracking-wide'>
                    Created At
                  </span>
                  <div className='text-sm text-gray-700 dark:text-gray-300 flex items-center gap-1.5'>
                    <CalendarIcon className='h-4 w-4 text-gray-500 dark:text-gray-400 shrink-0' />
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
        <div className='px-6 py-4 border-t border-gray-100 dark:border-gray-700 flex flex-wrap items-center justify-between gap-4'>
          <div>
            {isEditMode && !isReadOnly && (
              <button
                onClick={handleDelete}
                disabled={loading}
                className='inline-flex items-center px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm font-medium text-red-500 dark:text-red-400 bg-white dark:bg-gray-800 hover:bg-red-50 dark:hover:bg-red-900/20 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors'
              >
                <TrashIcon className='h-4 w-4' />
              </button>
            )}
          </div>
          <div className='flex gap-3'>
            {isReadOnly ? (
              <button
                onClick={goBack}
                disabled={loading}
                className='min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm font-medium text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors'
              >
                Back
              </button>
            ) : (
              <>
                <button
                  onClick={goBack}
                  disabled={loading}
                  className='min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm font-medium text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors'
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={loading}
                  className='inline-flex items-center justify-center gap-2 min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-purple-700 hover:bg-purple-800 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors'
                >
                  {loading && <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-white' />}
                  {isEditMode ? 'Save Changes' : 'Register Agent'}
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default AgentRegistryOrEdit;
