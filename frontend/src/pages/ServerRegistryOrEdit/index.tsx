import { TrashIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useState } from 'react';
import { HiOutlineShare } from 'react-icons/hi2';
import { useNavigate, useSearchParams } from 'react-router-dom';

import McpIcon from '@/assets/McpIcon';
import ShareModal from '@/components/ShareModal';
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
  const [shareOpen, setShareOpen] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [serverDetail, setServerDetail] = useState<GetServersDetailResponse | null>(null);
  const [formData, setFormData] = useState<ServerConfig>(INIT_DATA);
  const [originalData, setOriginalData] = useState<ServerConfig | null>(null);
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});

  const isEditMode = !!id;
  const isReadOnly = searchParams.get('isReadOnly') === 'true';
  const fromTab = searchParams.get('fromTab');

  useEffect(() => {
    if (id) getDetail();
  }, [id]);

  const goBack = () => {
    if (fromTab) {
      navigate(`/?tab=${fromTab}`, { replace: true });
    } else {
      navigate(-1);
    }
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
        <McpPlaygroundModal serverName={serverDetail?.serverName || ''} onClose={() => setPlaygroundOpen(false)} />
      )}
      {shareOpen && id && (
        <ShareModal
          itemName={formData.title || serverDetail?.title || 'MCP Server'}
          resourceId={id}
          isOpen={shareOpen}
          onClose={() => setShareOpen(false)}
        />
      )}
      <div className='h-full overflow-y-auto custom-scrollbar -mr-4 sm:-mr-6 lg:-mr-8'>
        <div className='mx-auto flex min-h-full w-3/4 flex-col rounded-lg bg-[var(--jarvis-card)]'>
          {/* Header */}
          <div className='flex items-center gap-4 border-b border-[color:var(--jarvis-border)] px-6 py-6'>
            <div className='flex items-center justify-center rounded-xl bg-[var(--jarvis-primary-soft)] p-3'>
              <McpIcon className='h-8 w-8 text-[var(--jarvis-primary-text)]' />
            </div>
            <div>
              <h1 className='text-2xl font-bold text-[var(--jarvis-text-strong)]'>
                {isReadOnly ? 'View MCP Server' : isEditMode ? 'Edit MCP Server' : 'Register MCP Server'}
              </h1>
              <p className='mt-0.5 text-base text-[var(--jarvis-muted)]'>
                Configure a Model Context Protocol server
              </p>
            </div>
          </div>
          {/* Content */}
          <div className='px-6 py-4 flex-1 flex flex-col'>
            {loadingDetail ? (
              <div className='flex-1 flex items-center justify-center min-h-[200px]'>
                <div className='h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]'></div>
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
          <div className='flex flex-wrap items-center justify-between gap-4 border-t border-[color:var(--jarvis-border)] px-6 py-4'>
          <div className='flex items-center gap-3'>
              {isEditMode && !isReadOnly && serverDetail?.permissions?.DELETE && (
                <button
                  onClick={handleDelete}
                  disabled={loading}
                  className='inline-flex items-center rounded-md border border-transparent bg-[var(--jarvis-danger-soft)] px-4 py-2 text-sm font-medium text-[var(--jarvis-danger-text)] shadow-sm hover:bg-[var(--jarvis-danger)]/20 focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-danger)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50'
                >
                  <TrashIcon className='h-4 w-4' />
                </button>
              )}
              {isEditMode && !!id && serverDetail?.permissions?.SHARE && (
                <button
                  onClick={() => setShareOpen(true)}
                  disabled={loading || loadingDetail}
                  className='inline-flex items-center rounded-md border border-transparent bg-[var(--jarvis-primary-soft)] px-4 py-2 text-sm font-medium text-[var(--jarvis-primary-text)] shadow-sm hover:bg-[var(--jarvis-primary)]/20 focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50'
                >
                  <HiOutlineShare className='h-4 w-4' />
                </button>
              )}
            </div>
            <div className='flex gap-3'>
              <button
                onClick={goBack}
                disabled={loading}
                className='min-w-[80px] rounded-md border border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] px-4 py-2 text-sm font-medium text-[var(--jarvis-text)] shadow-sm focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 sm:min-w-[120px] md:min-w-[160px]'
              >
                Cancel
              </button>

              {isReadOnly && (
                <button
                  onClick={() => setPlaygroundOpen(true)}
                  disabled={loading || loadingDetail}
                  className='min-w-[80px] rounded-md border border-[var(--jarvis-primary)]/30 bg-[var(--jarvis-input-bg)] px-4 py-2 text-sm font-medium text-[var(--jarvis-primary-text)] shadow-sm focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 sm:min-w-[120px] md:min-w-[160px]'
                >
                  Playground
                </button>
              )}
              {!isReadOnly && (
                <button
                  onClick={handleSave}
                  disabled={loading}
                  className='inline-flex min-w-[80px] items-center justify-center gap-2 rounded-md border border-transparent bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-[var(--jarvis-primary-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 sm:min-w-[120px] md:min-w-[160px]'
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
