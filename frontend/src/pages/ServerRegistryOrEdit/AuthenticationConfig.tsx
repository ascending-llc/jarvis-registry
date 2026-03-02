import { ClipboardDocumentIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import FormFields from '@/components/FormFields';
import { getBasePath } from '@/config';
import SERVICES from '@/services';
import Request from '@/services/request';
import type { AuthenticationConfig as AuthConfigType } from './types';

interface AuthenticationConfigProps {
  config: AuthConfigType;
  onChange: (config: AuthConfigType) => void;
  errors?: Record<string, string | undefined>;
  path?: string;
  mcpUrl?: string;
  isEditMode?: boolean;
  isReadOnly?: boolean;
  hasOauth?: boolean;
}

const DISCOVER_CANCEL_KEY = 'discoverOAuthEndpoints';

const AuthenticationConfig: React.FC<AuthenticationConfigProps> = ({
  config,
  onChange,
  errors = {},
  path = '',
  mcpUrl = '',
  isEditMode = false,
  isReadOnly = false,
  hasOauth = false,
}) => {
  const [isApiKeyDirty, setIsApiKeyDirty] = useState(false);
  const [isClientSecretDirty, setIsClientSecretDirty] = useState(false);
  const [isDiscoverLoading, setIsDiscoverLoading] = useState(false);
  const [registrationEndpoint, setRegistrationEndpoint] = useState<string | null>(null);
  const [isAuthUrlAutoFilled, setIsAuthUrlAutoFilled] = useState(false);
  const [isTokenUrlAutoFilled, setIsTokenUrlAutoFilled] = useState(false);

  const configRef = useRef(config);
  configRef.current = config;

  const updateConfig = (updates: Partial<AuthConfigType>) => {
    onChange({ ...config, ...updates });
  };

  useEffect(() => {
    if (config.type === 'oauth' && mcpUrl && isEditMode) {
      handleGetDiscover();
    }
    return () => {
      const cancel = Request.cancels[DISCOVER_CANCEL_KEY];
      if (cancel) {
        cancel();
        delete Request.cancels[DISCOVER_CANCEL_KEY];
      }
    };
  }, [mcpUrl, isEditMode]);

  const handleGetDiscover = async (baseConfig?: AuthConfigType) => {
    if (!mcpUrl) return;
    const existingCancel = Request.cancels[DISCOVER_CANCEL_KEY];
    if (existingCancel) {
      existingCancel();
      delete Request.cancels[DISCOVER_CANCEL_KEY];
    }
    setIsDiscoverLoading(true);
    setRegistrationEndpoint(null);
    setIsAuthUrlAutoFilled(false);
    setIsTokenUrlAutoFilled(false);
    onChange({ ...(baseConfig || configRef.current), use_dynamic_registration: false });
    try {
      const res = await SERVICES.MCP.getDiscover(mcpUrl, { cancelTokenKey: DISCOVER_CANCEL_KEY });
      if (res?.Code === -200) return;
      if (res?.metadata) {
        const { authorization_endpoint, token_endpoint, registration_endpoint } = res.metadata;
        const currentConfig = baseConfig || configRef.current;
        const useDynamicRegistration = registration_endpoint && !hasOauth;
        if (useDynamicRegistration || isEditMode) setRegistrationEndpoint(registration_endpoint);
        if (!isEditMode && authorization_endpoint) setIsAuthUrlAutoFilled(true);
        if (!isEditMode && token_endpoint) setIsTokenUrlAutoFilled(true);
        onChange({
          ...currentConfig,
          authorization_url: authorization_endpoint || currentConfig.authorization_url,
          token_url: token_endpoint || currentConfig.token_url,
          use_dynamic_registration: useDynamicRegistration,
        });
      }
    } catch (error) {
      console.error('Failed to discover OAuth endpoints:', error);
    } finally {
      setIsDiscoverLoading(false);
    }
  };

  const handleTypeChange = (type: AuthConfigType['type']) => {
    const nextConfig = { ...config, type };
    onChange(nextConfig);
    if (type === 'oauth' && !isReadOnly && mcpUrl && !config.authorization_url && !config.token_url) {
      handleGetDiscover(nextConfig);
    }
  };

  const cleanPath = path?.replace(/\/+$/, '') || '';
  const serverPath = cleanPath && !cleanPath.startsWith('/') ? `/${cleanPath}` : cleanPath;
  const redirectUri = `${window.location.protocol}//${window.location.host}${getBasePath()}/api/v1/mcp${serverPath}/oauth/callback`;

  return (
    <div className='space-y-6'>
      <div>
        <h3 className='text-lg font-semibold text-gray-900 dark:text-white mb-2'>Authentication</h3>
        <div className='space-y-4'>
          <div>
            <FormFields.RadioGroupField
              label='Authentication Type'
              value={config.type}
              onChange={val => handleTypeChange(val)}
              options={[
                { label: 'No Auth', value: 'auto' },
                { label: 'API Key', value: 'apiKey' },
                { label: 'OAuth', value: 'oauth' },
              ]}
              disabled={isReadOnly}
            />
          </div>

          {config.type === 'auto' && (
            <div className='rounded-md bg-gray-50 dark:bg-gray-700/50 p-4 text-sm text-gray-600 dark:text-gray-300'>
              Choose this if your MCP server has no auth requirements.
            </div>
          )}

          {config.type === 'apiKey' && (
            <div className='space-y-4 animate-fadeIn'>
              <div>
                <label className='block text-sm font-medium text-gray-900 dark:text-gray-100 mb-2'>
                  API Key Source
                </label>
                <FormFields.CheckboxField
                  id='apiKeySource'
                  disabled={isReadOnly}
                  checked={config.source === 'user'}
                  onChange={e => updateConfig({ source: e.target.checked ? 'user' : 'admin' })}
                  label='Each user provides their own key'
                  description='When unchecked, an admin-provided key is used for all users.'
                />
              </div>

              {config.source === 'admin' && (
                <FormFields.InputField
                  label='API Key'
                  type='password'
                  showPasswordToggle={!isEditMode || isApiKeyDirty}
                  monospace
                  disabled={isReadOnly}
                  value={isEditMode && !isApiKeyDirty ? '' : config.key || ''}
                  placeholder={isEditMode && !isApiKeyDirty && config.key ? config.key : '...'}
                  onChange={e => {
                    setIsApiKeyDirty(true);
                    updateConfig({ key: e.target.value });
                  }}
                  helperText='Leave empty if using OAuth or no authentication'
                  error={errors.key}
                />
              )}

              <div>
                <FormFields.RadioGroupField
                  label='Header Format'
                  value={config.authorization_type || 'bearer'}
                  onChange={val => updateConfig({ authorization_type: val })}
                  options={[
                    { label: 'Bearer', value: 'bearer' },
                    { label: 'Basic', value: 'basic' },
                    { label: 'Custom', value: 'custom' },
                  ]}
                  disabled={isReadOnly}
                />

                {config.authorization_type === 'custom' && (
                  <div className='mt-4 animate-fadeIn'>
                    <FormFields.InputField
                      label='Custom Header Name'
                      required
                      disabled={isReadOnly}
                      value={config.custom_header || ''}
                      onChange={e => updateConfig({ custom_header: e.target.value })}
                      placeholder='X-Custom-Auth'
                      error={errors.custom_header}
                    />
                  </div>
                )}
              </div>
            </div>
          )}

          {config.type === 'oauth' && (
            <div className='space-y-4 animate-fadeIn'>
              {isDiscoverLoading ? (
                <div className='flex items-center justify-center py-8 space-x-3 text-sm text-gray-500 dark:text-gray-400'>
                  <div className='animate-spin rounded-full h-5 w-5 border-b-2 border-purple-600' />
                  <span>Discovering…</span>
                </div>
              ) : (
                <>
                  {registrationEndpoint && (
                    <div className='rounded-lg border border-purple-200 dark:border-purple-700 bg-purple-50 dark:bg-purple-900/30 p-4'>
                      <p className='text-sm font-semibold text-purple-700 dark:text-purple-300 mb-1'>
                        🔒 Dynamic Client Registration
                      </p>
                      <p className='text-xs font-mono text-gray-500 dark:text-gray-400 break-all'>
                        {registrationEndpoint}
                      </p>
                    </div>
                  )}

                  <FormFields.CheckboxField
                    id='useDynamicRegistration'
                    disabled={isReadOnly}
                    checked={config.use_dynamic_registration ?? false}
                    onChange={e => updateConfig({ use_dynamic_registration: e.target.checked })}
                    label='Use Dynamic Client Registration'
                    description='Client will be registered dynamically at startup. No manual credentials needed.'
                  />

                  {!(config.use_dynamic_registration ?? false) && (
                    <>
                      <FormFields.InputField
                        label='Client ID'
                        disabled={isReadOnly}
                        placeholder='your-client-id-here'
                        value={config.client_id || ''}
                        onChange={e => updateConfig({ client_id: e.target.value })}
                        helperText='Required for static clients. Leave blank if using dynamic client registration'
                        error={errors.client_id}
                      />

                      <FormFields.InputField
                        label='Client Secret'
                        type='password'
                        showPasswordToggle={!isEditMode || isClientSecretDirty}
                        monospace
                        disabled={isReadOnly}
                        value={isEditMode && !isClientSecretDirty ? '' : config.client_secret || ''}
                        placeholder={
                          isEditMode && !isClientSecretDirty && config.client_secret
                            ? config.client_secret
                            : 'your-client-secret-here'
                        }
                        onChange={e => {
                          setIsClientSecretDirty(true);
                          updateConfig({ client_secret: e.target.value });
                        }}
                        helperText='Required for static clients. Never share this value.'
                        error={errors.client_secret}
                      />

                      <FormFields.InputField
                        label='Authorization URL'
                        labelTag={!isEditMode && isAuthUrlAutoFilled ? 'Auto-Filled' : undefined}
                        required
                        type='url'
                        disabled={isReadOnly}
                        placeholder='https://auth.example.com/oauth/authorize'
                        value={config.authorization_url || ''}
                        onChange={e => {
                          setIsAuthUrlAutoFilled(false);
                          updateConfig({ authorization_url: e.target.value });
                        }}
                        helperText='The endpoint where users are redirected to authenticate and grant permissions.'
                        error={errors.authorization_url}
                      />

                      <FormFields.InputField
                        label='Token URL'
                        labelTag={!isEditMode && isTokenUrlAutoFilled ? 'Auto-Filled' : undefined}
                        required
                        type='url'
                        disabled={isReadOnly}
                        placeholder='https://auth.example.com/oauth/token'
                        value={config.token_url || ''}
                        onChange={e => {
                          setIsTokenUrlAutoFilled(false);
                          updateConfig({ token_url: e.target.value });
                        }}
                        helperText='The backend endpoint for exchanging authorization codes for access tokens.'
                        error={errors.token_url}
                      />

                      <FormFields.InputField
                        label='Scope'
                        disabled={isReadOnly}
                        placeholder='read write'
                        value={config.scope || ''}
                        onChange={e => updateConfig({ scope: e.target.value })}
                        helperText={
                          <>
                            <span className='block'>Space-separated list of permissions.Examples:</span>
                            <span className='block'>
                              Generic: <span className='italic'>read write profile</span> • GitHub:
                              <span className='italic'>repo read:user</span> • Google:
                              <span className='italic'>openid email profile</span>
                            </span>
                          </>
                        }
                      />

                      <FormFields.InputField
                        label='Redirect URI'
                        readOnly
                        disabled={isReadOnly}
                        value={redirectUri}
                        inputClassName='cursor-not-allowed'
                        suffix={
                          <button
                            type='button'
                            onClick={() => {
                              navigator.clipboard.writeText(redirectUri);
                            }}
                            className='btn-input-suffix'
                          >
                            <ClipboardDocumentIcon className='h-5 w-5' aria-hidden='true' />
                          </button>
                        }
                      />
                    </>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default AuthenticationConfig;