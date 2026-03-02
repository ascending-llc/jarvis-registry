import { TrashIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import McpIcon from '@/assets/McpIcon';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import type { GET_SERVERS_DETAIL_RESPONSE, Server } from '@/services/server/type';
import MainConfigForm from './MainConfigForm';
import type { AuthenticationConfig as AuthConfigType, ServerConfig } from './types';

const DEFAULT_AUTH_CONFIG: AuthConfigType = { type: 'auto', source: 'admin', authorization_type: 'bearer' };

const AUTH_ERROR_KEYS = ['key', 'custom_header', 'authorization_url', 'token_url'] as const;

const parseAuthConfig = (result: GET_SERVERS_DETAIL_RESPONSE): AuthConfigType => {
  if (result.apiKey) {
    return {
      type: 'apiKey',
      source: result.apiKey.source,
      authorization_type: result.apiKey.authorization_type,
      key: result.apiKey.key,
      custom_header: result.apiKey.custom_header,
    };
  }
  if (result.oauth || result.requiresOAuth) {
    return {
      type: 'oauth',
      client_id: result.oauth?.client_id,
      client_secret: result.oauth?.client_secret,
      authorization_url: result.oauth?.authorization_url,
      token_url: result.oauth?.token_url,
      scope: result.oauth?.scope,
      use_dynamic_registration: result.requiresOAuth && !result.oauth,
    };
  }
  return { ...DEFAULT_AUTH_CONFIG };
};

const processDataByAuthType = (data: ServerConfig, originalData: ServerConfig | null): Record<string, unknown> => {
  const baseData: Partial<Server> = {
    title: data.title,
    description: data.description,
    path: data.path,
    url: data.url,
    tags: data.tags,
    type: data.type,
    headers: data.headers && Object.keys(data.headers).length === 0 ? null : data.headers,
  };
  switch (data.authConfig.type) {
    case 'auto':
      return { ...baseData, apiKey: null, oauth: null, requiresOAuth: false };
    case 'apiKey':
      return {
        ...baseData,
        apiKey: {
          source: data.authConfig.source,
          authorization_type: data.authConfig.authorization_type,
          ...(data.authConfig.source !== 'user' &&
          data.authConfig.key &&
          data.authConfig.key !== originalData?.authConfig?.key
            ? { key: data.authConfig.key }
            : {}),
          ...(data.authConfig.authorization_type === 'custom' && data.authConfig.custom_header
            ? { custom_header: data.authConfig.custom_header }
            : {}),
        },
        oauth: null,
        requiresOAuth: false,
      };
    case 'oauth':
      return {
        ...baseData,
        oauth: data.authConfig.use_dynamic_registration
          ? null
          : {
              client_id: data.authConfig.client_id,
              ...(data.authConfig.client_secret !== originalData?.authConfig?.client_secret
                ? { client_secret: data.authConfig.client_secret }
                : {}),
              authorization_url: data.authConfig.authorization_url,
              token_url: data.authConfig.token_url,
              scope: data.authConfig.scope,
            },
        apiKey: null,
        requiresOAuth: true,
      };
    default:
      return {};
  }
};

const INIT_DATA: ServerConfig = {
  title: '',
  description: '',
  path: '',
  url: '',
  headers: { Authorization: '', 'x-jarvis-auth': '' },
  type: 'streamable-http',
  authConfig: DEFAULT_AUTH_CONFIG,
  trustServer: false,
  tags: [],
};

const ServerRegistryOrEdit: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const id = searchParams.get('id');
  const { showToast } = useGlobal();
  const { refreshServerData, handleServerUpdate } = useServer();

  const [loading, setLoading] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [serverDetail, setServerDetail] = useState<GET_SERVERS_DETAIL_RESPONSE | null>(null);
  const [formData, setFormData] = useState<ServerConfig>(INIT_DATA);
  const [originalData, setOriginalData] = useState<ServerConfig | null>(null);
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const [serverData, setServerData] = useState<{ serverName: string; path: string }>({ serverName: '', path: '' });
  const [showSuccessDialog, setShowSuccessDialog] = useState(false);

  const isEditMode = !!id;
  const isReadOnly = searchParams.get('isReadOnly') === 'true';

  useEffect(() => {
    if (id) getDetail();
  }, [id]);

  const goBack = () => {
    navigate(-1);
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !showSuccessDialog) {
        goBack();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showSuccessDialog]);

  const getDetail = async () => {
    if (!id) return;
    setLoadingDetail(true);
    try {
      const result = await SERVICES.SERVER.getServerDetail(id);
      const data: ServerConfig = {
        title: result.title,
        description: result.description,
        path: result.path,
        url: result.url || '',
        type: result.type,
        headers: result.headers || null,
        authConfig: parseAuthConfig(result),
        trustServer: true,
        tags: result.tags || [],
      };
      setServerDetail(result);
      setFormData(data);
      setOriginalData(data);
    } catch (_error) {
      showToast('Failed to fetch server details', 'error');
    } finally {
      setLoadingDetail(false);
    }
  };

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
      newErrors.url = 'MCP Server URL is required';
    }

    if (!formData.trustServer) {
      newErrors.trustServer = 'You must trust this application';
    }

    // Headers Validation
    if (formData.headers && Object.keys(formData.headers).length > 0) {
      const hasEmptyHeader = Object.entries(formData.headers).some(
        ([key, val]) => !key.trim() || !String(val ?? '').trim(),
      );
      if (hasEmptyHeader) {
        newErrors.headers = 'Header name and value cannot be empty';
      }
    }

    // Auth Validation
    const auth = formData.authConfig;
    if (auth.type === 'apiKey') {
      if (auth.source === 'admin' && !auth.key?.trim()) {
        newErrors.key = 'API Key is required';
      }
      if (auth.authorization_type === 'custom' && !auth.custom_header?.trim()) {
        newErrors.custom_header = 'Custom Header Name is required';
      }
    } else if (auth.type === 'oauth') {
      if (!auth.use_dynamic_registration) {
        if (!auth.authorization_url?.trim()) newErrors.authorization_url = 'Authorization URL is required';
        if (!auth.token_url?.trim()) newErrors.token_url = 'Token URL is required';
      }
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const updateField = (field: keyof ServerConfig, value: any) => {
    setFormData(prev => ({ ...prev, [field]: value }));

    if (field === 'authConfig') {
      setErrors(prev => {
        const next = { ...prev };
        let changed = false;
        for (const k of AUTH_ERROR_KEYS) {
          if (next[k] && (value as AuthConfigType)[k]?.toString().trim()) {
            next[k] = undefined;
            changed = true;
          }
        }
        return changed ? next : prev;
      });
    } else if (field === 'path') {
      const isInvalid = value && !/^\//.test(value as string);
      setErrors(prev => ({ ...prev, path: isInvalid ? 'Path must start with /' : undefined }));
    } else if (errors[field as string]) {
      setErrors(prev => ({ ...prev, [field as string]: undefined }));
    }
  };

  const handleDelete = async () => {
    if (!id) return;
    try {
      await SERVICES.SERVER.deleteServer(id);
      showToast('Server deleted successfully', 'success');
      navigate('/', { replace: true });
      refreshServerData(true);
    } catch (error: any) {
      showToast(error?.detail || error, 'error');
    }
  };

  const handleSave = async () => {
    if (!validate()) return;

    setLoading(true);
    const data: any = processDataByAuthType(formData, originalData);
    try {
      if (isEditMode) {
        const result = await SERVICES.SERVER.updateServer(id, data);
        showToast('Server updated successfully', 'success');
        handleServerUpdate(id, {
          title: result.title,
          description: result.description,
          path: result.path,
          url: result.url,
          tags: result.tags,
          last_checked_time: result.updatedAt ?? new Date().toISOString(),
        });
      } else {
        const result = await SERVICES.SERVER.createServer(data);
        setServerData(result);
        refreshServerData(true);
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
            <McpIcon className='h-8 w-8 text-purple-600 dark:text-purple-300' />
          </div>
          <div>
            <h1 className='text-2xl font-bold text-gray-900 dark:text-white'>
              {isReadOnly ? 'View MCP Server' : isEditMode ? 'Edit MCP Server' : 'Register MCP Server'}
            </h1>
            <p className='text-base text-gray-500 dark:text-gray-400 mt-0.5'>
              Configure a Model Context Protocol server
            </p>
          </div>
        </div>
        {/* Content */}
        <div className='px-6 py-4 flex-1 flex flex-col'>
          {loadingDetail ? (
            <div className='flex-1 flex items-center justify-center min-h-[200px]'>
              <div className='animate-spin rounded-full h-8 w-8 border-b-2 border-purple-600'></div>
            </div>
          ) : (
            <MainConfigForm
              formData={formData}
              serverDetail={serverDetail}
              updateField={updateField}
              errors={errors}
              isEditMode={isEditMode}
              isReadOnly={isReadOnly}
            />
          )}
        </div>
        {/* Footer */}
        <div className='px-6 py-4 border-t border-gray-100 dark:border-gray-700 flex flex-wrap items-center justify-between gap-4'>
          <div>
            {isEditMode && !isReadOnly && serverDetail?.permissions?.DELETE && (
              <button
                onClick={handleDelete}
                disabled={loading}
                className='inline-flex items-center px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm font-medium text-red-500 dark:text-red-400 bg-white dark:bg-gray-800 hover:bg-red-50 dark:hover:bg-red-900/20 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 disabled:opacity-50 disabled:cursor-not-allowed'
              >
                <TrashIcon className='h-4 w-4' />
              </button>
            )}
          </div>
          <div className='flex gap-3'>
            <button
              onClick={goBack}
              disabled={loading}
              className='min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm font-medium text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed'
            >
              Cancel
            </button>
            {!isReadOnly && (
              <button
                onClick={handleSave}
                disabled={loading}
                className='inline-flex items-center justify-center gap-2 min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-purple-700 hover:bg-purple-800 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed'
              >
                {loading && <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-white'></div>}
                {isEditMode ? 'Update' : 'Create'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ServerRegistryOrEdit;
