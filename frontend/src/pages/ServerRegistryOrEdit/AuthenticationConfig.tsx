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
    onChange({ ...(baseConfig || configRef.current), useDynamicRegistration: false });
    try {
      const res = await SERVICES.MCP.getDiscover(mcpUrl, { cancelTokenKey: DISCOVER_CANCEL_KEY });
      if (res?.Code === -200) return;
      if (res?.metadata) {
        const { authorizationEndpoint, tokenEndpoint, registrationEndpoint } = res.metadata;
        const currentConfig = baseConfig || configRef.current;
        const useDynamicRegistration = registrationEndpoint && !hasOauth;
        if (useDynamicRegistration || isEditMode) setRegistrationEndpoint(registrationEndpoint);
        const shouldFillAuthUrl = authorizationEndpoint && !currentConfig.authorizationUrl;
        const shouldFillTokenUrl = tokenEndpoint && !currentConfig.tokenUrl;
        if (shouldFillAuthUrl) setIsAuthUrlAutoFilled(true);
        if (shouldFillTokenUrl) setIsTokenUrlAutoFilled(true);
        onChange({
          ...currentConfig,
          authorizationUrl: currentConfig.authorizationUrl || authorizationEndpoint,
          tokenUrl: currentConfig.tokenUrl || tokenEndpoint,
          useDynamicRegistration: useDynamicRegistration,
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
    if (type === 'oauth' && !isReadOnly && mcpUrl && !config.authorizationUrl && !config.tokenUrl) {
      handleGetDiscover(nextConfig);
    }
  };

  const cleanPath = path?.replace(/\/+$/, '') || '';
  const serverPath = cleanPath && !cleanPath.startsWith('/') ? `/${cleanPath}` : cleanPath;
  const redirectUri = `${window.location.protocol}//${window.location.host}${getBasePath()}/api/v1/mcp${serverPath}/oauth/callback`;

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold text-[var(--jarvis-text-strong)] mb-2">Authentication</h3>
        <div className="space-y-4">
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
            <div className="rounded-md bg-[var(--jarvis-bg)] bg-[var(--jarvis-card-muted)] p-4 text-sm text-[var(--jarvis-muted)] text-[var(--jarvis-text)]">
              Choose this if your MCP server has no auth requirements.
            </div>
          )}

          {config.type === 'apiKey' && (
            <div className="space-y-4 animate-fadeIn">
              <div>
                <label className="block text-sm font-medium text-[var(--jarvis-text-strong)] mb-2">
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
                  value={config.authorizationType || 'bearer'}
                  onChange={val => updateConfig({ authorizationType: val })}
                  options={[
                    { label: 'Bearer', value: 'bearer' },
                    { label: 'Basic', value: 'basic' },
                    { label: 'Custom', value: 'custom' },
                  ]}
                  disabled={isReadOnly}
                />

                {config.authorizationType === 'custom' && (
                  <div className="mt-4 animate-fadeIn">
                    <FormFields.InputField
                      label='Custom Header Name'
                      required
                      disabled={isReadOnly}
                      value={config.customHeader || ''}
                      onChange={e => updateConfig({ customHeader: e.target.value })}
                      placeholder='X-Custom-Auth'
                      error={errors.customHeader}
                    />
                  </div>
                )}
              </div>
            </div>
          )}

          {config.type === 'oauth' && (
            <div className="space-y-4 animate-fadeIn">
              {isDiscoverLoading ? (
                <div className="flex items-center justify-center py-8 space-x-3 text-sm text-[var(--jarvis-muted)]">
                  <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-[var(--jarvis-primary)]" />
                  <span>Discovering…</span>
                </div>
              ) : (
                <>
                  {registrationEndpoint && (
                    <div className="rounded-lg border border-[var(--jarvis-primary-soft)] bg-[var(--jarvis-primary-soft)] p-4">
                      <p className="text-sm font-semibold text-[var(--jarvis-primary)] mb-1">
                        🔒 Dynamic Client Registration
                      </p>
                      <p className="text-xs font-mono text-[var(--jarvis-muted)] break-all">
                        {registrationEndpoint}
                      </p>
                    </div>
                  )}

                  <FormFields.CheckboxField
                    id='useDynamicRegistration'
                    disabled={isReadOnly}
                    checked={config.useDynamicRegistration ?? false}
                    onChange={e => updateConfig({ useDynamicRegistration: e.target.checked })}
                    label='Use Dynamic Client Registration'
                    description='Client will be registered dynamically at startup. No manual credentials needed.'
                  />

                  {!(config.useDynamicRegistration ?? false) && (
                    <>
                      <FormFields.InputField
                        label='Client ID'
                        disabled={isReadOnly}
                        placeholder='your-client-id-here'
                        value={config.clientId || ''}
                        onChange={e => updateConfig({ clientId: e.target.value })}
                        helperText='Required for static clients. Leave blank if using dynamic client registration'
                        error={errors.clientId}
                      />

                      <FormFields.InputField
                        label='Client Secret'
                        type='password'
                        showPasswordToggle={!isEditMode || isClientSecretDirty}
                        monospace
                        disabled={isReadOnly}
                        value={isEditMode && !isClientSecretDirty ? '' : config.clientSecret || ''}
                        placeholder={
                          isEditMode && !isClientSecretDirty && config.clientSecret
                            ? config.clientSecret
                            : 'your-client-secret-here'
                        }
                        onChange={e => {
                          setIsClientSecretDirty(true);
                          updateConfig({ clientSecret: e.target.value });
                        }}
                        helperText='Required for static clients. Never share this value.'
                        error={errors.clientSecret}
                      />

                      <FormFields.InputField
                        label='Authorization URL'
                        labelTag={isAuthUrlAutoFilled ? 'Auto-Filled' : undefined}
                        required
                        type='url'
                        disabled={isReadOnly}
                        placeholder='https://auth.example.com/oauth/authorize'
                        value={config.authorizationUrl || ''}
                        onChange={e => {
                          setIsAuthUrlAutoFilled(false);
                          updateConfig({ authorizationUrl: e.target.value });
                        }}
                        helperText='The endpoint where users are redirected to authenticate and grant permissions.'
                        error={errors.authorizationUrl}
                      />

                      <FormFields.InputField
                        label='Token URL'
                        labelTag={isTokenUrlAutoFilled ? 'Auto-Filled' : undefined}
                        required
                        type='url'
                        disabled={isReadOnly}
                        placeholder='https://auth.example.com/oauth/token'
                        value={config.tokenUrl || ''}
                        onChange={e => {
                          setIsTokenUrlAutoFilled(false);
                          updateConfig({ tokenUrl: e.target.value });
                        }}
                        helperText='The backend endpoint for exchanging authorization codes for access tokens.'
                        error={errors.tokenUrl}
                      />

                      <FormFields.InputField
                        label='Scope'
                        disabled={isReadOnly}
                        placeholder='read write'
                        value={config.scope || ''}
                        onChange={e => updateConfig({ scope: e.target.value })}
                        helperText={
                          <>
                            <span className="block">Space-separated list of permissions.Examples:</span>
                            <span className="block">
                              Generic: <span className="italic">read write profile</span> • GitHub:
                              <span className="italic">repo read:user</span> • Google:
                              <span className="italic">openid email profile</span>
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
                            className="btn-input-suffix"
                          >
                            <ClipboardDocumentIcon className="h-5 w-5" aria-hidden='true' />
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
