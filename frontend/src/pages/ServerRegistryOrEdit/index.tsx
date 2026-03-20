import { TrashIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import McpIcon from '@/assets/McpIcon';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import type { GetServersDetailResponse, Server } from '@/services/server/type';
import MainConfigForm from './MainConfigForm';
import McpPlaygroundModal from './McpPlaygroundModal';
import type { AuthenticationConfig as AuthConfigType, ServerConfig } from './types';

const DEFAULT_AUTH_CONFIG: AuthConfigType = { type: 'auto', source: 'admin', authorizationType: 'bearer' };

const AUTH_ERROR_KEYS = ['key', 'customHeader', 'authorizationUrl', 'tokenUrl'] as const;

const parseAuthConfig = (result: GetServersDetailResponse): AuthConfigType => {
  if (result.apiKey) {
    return {
      type: 'apiKey',
      source: result.apiKey.source,
      authorizationType: result.apiKey.authorizationType,
      key: result.apiKey.key,
      customHeader: result.apiKey.customHeader,
    };
  }
  if (result.oauth || result.requiresOauth) {
    return {
      type: 'oauth',
      clientId: result.oauth?.clientId,
      clientSecret: result.oauth?.clientSecret,
      authorizationUrl: result.oauth?.authorizationUrl,
      tokenUrl: result.oauth?.tokenUrl,
      scope: result.oauth?.scope,
      useDynamicRegistration: result.requiresOauth && !result.oauth,
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
      return { ...baseData, apiKey: null, oauth: null, requiresOauth: false };
    case 'apiKey':
      return {
        ...baseData,
        apiKey: {
          source: data.authConfig.source,
          authorizationType: data.authConfig.authorizationType,
          ...(data.authConfig.source !== 'user' &&
          data.authConfig.key &&
          data.authConfig.key !== originalData?.authConfig?.key
            ? { key: data.authConfig.key }
            : {}),
          ...(data.authConfig.authorizationType === 'custom' && data.authConfig.customHeader
            ? { customHeader: data.authConfig.customHeader }
            : {}),
        },
        oauth: null,
        requiresOauth: false,
      };
    case 'oauth':
      return {
        ...baseData,
        oauth: data.authConfig.useDynamicRegistration
          ? null
          : {
              clientId: data.authConfig.clientId,
              ...(data.authConfig.clientSecret !== originalData?.authConfig?.clientSecret
                ? { clientSecret: data.authConfig.clientSecret }
                : {}),
              authorizationUrl: data.authConfig.authorizationUrl,
              tokenUrl: data.authConfig.tokenUrl,
              scope: data.authConfig.scope,
            },
        apiKey: null,
        requiresOauth: true,
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
  headers: null,
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
  const [playgroundOpen, setPlaygroundOpen] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [serverDetail, setServerDetail] = useState<GetServersDetailResponse | null>(null);
  const [formData, setFormData] = useState<ServerConfig>(INIT_DATA);
  const [originalData, setOriginalData] = useState<ServerConfig | null>(null);
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});

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
      if (e.key === 'Escape') {
        goBack();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

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
    } else if (!/^\/[a-zA-Z0-9\-._~%@!$&'()*+,;=:/]*$/.test(formData.path)) {
      newErrors.path = 'Path contains invalid characters';
    }

    if (!formData.url?.trim()) {
      newErrors.url = 'MCP Server URL is required';
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
      if (auth.authorizationType === 'custom' && !auth.customHeader?.trim()) {
        newErrors.customHeader = 'Custom Header Name is required';
      }
    } else if (auth.type === 'oauth') {
      if (!auth.useDynamicRegistration) {
        if (!auth.authorizationUrl?.trim()) newErrors.authorizationUrl = 'Authorization URL is required';
        if (!auth.tokenUrl?.trim()) newErrors.tokenUrl = 'Token URL is required';
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
          lastCheckedTime: result.updatedAt ?? new Date().toISOString(),
        });
      } else {
        await SERVICES.SERVER.createServer(data);
        showToast('Server created successfully', 'success');
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
    <>
      {playgroundOpen && (
        <McpPlaygroundModal
          serverPath={serverDetail?.serverName || formData.path}
          onClose={() => setPlaygroundOpen(false)}
        />
      )}
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
            {isReadOnly && (
              <button
                onClick={() => setPlaygroundOpen(true)}
                disabled={loading || loadingDetail}
                className='min-w-[80px] sm:min-w-[120px] md:min-w-[160px] px-4 py-2 border border-purple-300 dark:border-purple-600 rounded-md shadow-sm text-sm font-medium text-purple-700 dark:text-purple-300 bg-white dark:bg-gray-800 hover:bg-purple-50 dark:hover:bg-purple-900/20 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed'
              >
                Playground
            </button>)
            }
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
    </>
  );
};

export default ServerRegistryOrEdit;
