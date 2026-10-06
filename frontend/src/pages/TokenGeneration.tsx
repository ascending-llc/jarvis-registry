import { CheckIcon, ClipboardIcon, ExclamationTriangleIcon, KeyIcon } from '@heroicons/react/24/outline';
import { isAxiosError } from 'axios';
import type React from 'react';
import { useState } from 'react';
import IconButton from '@/components/IconButton';
import SERVICES from '@/services';
import { type GetTokenRequest, type GetTokenResponse, TokenPurpose } from '@/services/auth/type';
import { useAuth } from '../contexts/AuthContext';

const GENERIC_GENERATE_ERROR = 'Failed to generate token';

type CustomScopesParseResult = { scopes: string[]; error: null } | { scopes: null; error: string };

// Custom mode must always send a non-empty requestedScopes: the backend treats an omitted list as
// "use my current scopes" and rejects an empty one.
const parseCustomScopes = (text: string): CustomScopesParseResult => {
  const trimmed = text.trim();
  if (!trimmed) {
    return { scopes: null, error: 'Enter a JSON array with at least one scope' };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (_e) {
    return { scopes: null, error: 'Invalid JSON format' };
  }
  if (!Array.isArray(parsed)) {
    return { scopes: null, error: 'Custom scopes must be a JSON array' };
  }
  if (parsed.length === 0) {
    return { scopes: null, error: 'Enter a JSON array with at least one scope' };
  }
  // The backend rejects non-string items with a 422, which would only surface as the generic error
  if (!parsed.every((scope): scope is string => typeof scope === 'string' && scope.trim() !== '')) {
    return { scopes: null, error: 'Scopes must be non-empty strings' };
  }
  return { scopes: parsed, error: null };
};

const TokenGeneration: React.FC = () => {
  const { user } = useAuth();
  const [formData, setFormData] = useState({
    description: '',
    expiresInHours: 8,
    scopeMethod: 'current' as 'current' | 'custom',
    customScopes: '',
    tokenPurpose: TokenPurpose.Interactive,
  });
  const [generatedToken, setGeneratedToken] = useState<string>('');
  const [tokenDetails, setTokenDetails] = useState<GetTokenResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [copiedTarget, setCopiedTarget] = useState<'token' | 'cli-helper' | null>(null);
  const [error, setError] = useState<string>('');

  const expirationOptions = [
    { value: 1, label: '1 hour' },
    { value: 8, label: '8 hours' },
    { value: 24, label: '24 hours' },
  ];

  const handleGenerateToken = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setGeneratedToken('');
    setTokenDetails(null);

    const requestData: GetTokenRequest = {
      description: formData.description,
      expiresInHours: formData.expiresInHours,
      tokenPurpose: formData.tokenPurpose,
    };

    // In current mode requestedScopes is omitted, so the backend uses all of the user's token-eligible scopes
    if (formData.scopeMethod === 'custom') {
      const { scopes, error: parseError } = parseCustomScopes(formData.customScopes);
      if (parseError !== null) {
        setError(parseError);
        return;
      }
      requestData.requestedScopes = scopes;
    }

    setLoading(true);
    try {
      const response = await SERVICES.AUTH.getToken(requestData);

      if (response.success) {
        setGeneratedToken(response.tokenData.accessToken);
        setTokenDetails(response);
      } else {
        throw new Error('Token generation failed');
      }
    } catch (err) {
      console.error('Failed to generate token:', err);
      // A 422 validation error carries a list detail; only show a detail the backend wrote as a message
      const detail = isAxiosError<{ detail?: unknown }>(err) ? err.response?.data?.detail : undefined;
      setError(typeof detail === 'string' && detail ? detail : GENERIC_GENERATE_ERROR);
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = async (value: string, target: 'token' | 'cli-helper') => {
    const showCopiedFeedback = () => {
      setCopiedTarget(target);
      setTimeout(() => {
        setCopiedTarget(currentTarget => (currentTarget === target ? null : currentTarget));
      }, 2000);
    };

    try {
      await navigator.clipboard.writeText(value);
      showCopiedFeedback();
    } catch (_error) {
      // Fallback for older browsers
      const textArea = document.createElement('textarea');
      textArea.value = value;
      textArea.style.position = 'fixed';
      textArea.style.left = '-999999px';
      textArea.style.top = '-999999px';
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();

      try {
        document.execCommand('copy');
        showCopiedFeedback();
      } catch (err) {
        console.error('Failed to copy value:', err);
      }

      document.body.removeChild(textArea);
    }
  };

  const scopeValidationError =
    formData.scopeMethod === 'custom' ? parseCustomScopes(formData.customScopes).error : null;
  const cliHelper = `export AUTH_TOKEN="${generatedToken}"`;

  return (
    <div className='flex flex-col h-full'>
      {/* Compact Header Section */}
      <div className='flex-shrink-0 pb-2'>
        <div className='text-center'>
          <div className='mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-[var(--jarvis-primary-soft)]'>
            <KeyIcon className='h-5 w-5 text-[var(--jarvis-primary-text)]' />
          </div>
          <h1 className='text-xl font-bold text-[var(--jarvis-text-strong)]'>Generate JWT Token</h1>
          <p className='text-sm text-[var(--jarvis-muted)]'>
            Generate a personal access token for programmatic access to MCP servers
          </p>
        </div>
      </div>

      {/* Scrollable Content Area */}
      <div className='flex-1 overflow-y-auto min-h-0'>
        <div className='max-w-4xl mx-auto space-y-4 pb-6'>
          {/* Current User Permissions - Compact */}
          <div className='card bg-[var(--jarvis-card-muted)] p-4'>
            <h3 className='mb-2 text-base font-semibold text-[var(--jarvis-text-strong)]'>Your Current Permissions</h3>
            <div className='mb-2'>
              <span className='text-xs font-medium text-[var(--jarvis-text)]'>Scopes available for tokens:</span>
              <div className='flex flex-wrap gap-1 mt-1'>
                {user?.tokenScopes && user.tokenScopes.length > 0 ? (
                  user.tokenScopes.map(scope => (
                    <span
                      key={scope}
                      className='inline-flex items-center rounded-full bg-[var(--jarvis-info-soft)] px-2 py-0.5 text-xs font-medium text-[var(--jarvis-info-text)]'
                    >
                      {scope}
                    </span>
                  ))
                ) : (
                  <span className='text-xs text-[var(--jarvis-muted)]'>No scopes available</span>
                )}
              </div>
            </div>
            <p className='text-xs text-[var(--jarvis-muted)]'>
              <em>Generated tokens can have the same or fewer permissions than the scopes available for tokens.</em>
            </p>
          </div>

          {/* Token Configuration Form */}
          <div className='card p-4'>
            <form onSubmit={handleGenerateToken} className='space-y-4'>
              <h3 className='text-base font-semibold text-[var(--jarvis-text-strong)]'>Token Configuration</h3>

              {/* Form Fields - Responsive Grid */}
              <div className='grid grid-cols-1 lg:grid-cols-2 gap-4'>
                {/* Left Column */}
                <div className='space-y-3'>
                  {/* Description */}
                  <div>
                    <label htmlFor='description' className='mb-1 block text-sm font-medium text-[var(--jarvis-text)]'>
                      Description (optional)
                    </label>
                    <input
                      type='text'
                      id='description'
                      className='input text-sm'
                      placeholder='e.g., Token for automation script'
                      value={formData.description}
                      onChange={e => setFormData(prev => ({ ...prev, description: e.target.value }))}
                    />
                  </div>

                  {/* Expiration */}
                  <div>
                    <label
                      htmlFor='expiresInHours'
                      className='mb-1 block text-sm font-medium text-[var(--jarvis-text)]'
                    >
                      Expires In
                    </label>
                    <select
                      id='expiresInHours'
                      className='input text-sm'
                      value={formData.expiresInHours}
                      onChange={e => setFormData(prev => ({ ...prev, expiresInHours: parseInt(e.target.value, 10) }))}
                    >
                      {expirationOptions.map(option => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* Right Column */}
                <div className='space-y-6'>
                  {/* Scope Configuration */}
                  <div>
                    <h4 className='mb-2 text-sm font-semibold text-[var(--jarvis-text-strong)]'>Scope Configuration</h4>

                    <div className='space-y-3'>
                      <label
                        className={`flex items-start space-x-3 p-3 rounded-xl border cursor-pointer transition-all duration-200 ${
                          formData.scopeMethod === 'current'
                            ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary)]/5 ring-1 ring-[var(--jarvis-primary)]/20'
                            : 'border-[var(--jarvis-input-border)] hover:border-[var(--jarvis-primary)]/50 hover:bg-[var(--jarvis-card-muted)]'
                        }`}
                      >
                        <div className='mt-0.5 flex-shrink-0'>
                          <input
                            type='radio'
                            name='scopeMethod'
                            value='current'
                            checked={formData.scopeMethod === 'current'}
                            onChange={e =>
                              setFormData(prev => ({ ...prev, scopeMethod: e.target.value as 'current' | 'custom' }))
                            }
                            className='rounded-full border-[color:var(--jarvis-input-border)] text-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)] bg-transparent'
                          />
                        </div>
                        <div className='flex-1'>
                          <div
                            className={`text-sm font-medium ${formData.scopeMethod === 'current' ? 'text-[var(--jarvis-primary)]' : 'text-[var(--jarvis-text-strong)]'}`}
                          >
                            Use my current scopes
                          </div>
                          <div className='text-xs text-[var(--jarvis-muted)] mt-1'>
                            Generate token with all your token-eligible scopes. Non-interactive agent tokens keep only{' '}
                            <code>mcp-proxy-ops</code> and <code>a2a-proxy-ops</code>.
                          </div>
                        </div>
                      </label>

                      <label
                        className={`flex items-start space-x-3 p-3 rounded-xl border cursor-pointer transition-all duration-200 ${
                          formData.scopeMethod === 'custom'
                            ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary)]/5 ring-1 ring-[var(--jarvis-primary)]/20'
                            : 'border-[var(--jarvis-input-border)] hover:border-[var(--jarvis-primary)]/50 hover:bg-[var(--jarvis-card-muted)]'
                        }`}
                      >
                        <div className='mt-0.5 flex-shrink-0'>
                          <input
                            type='radio'
                            name='scopeMethod'
                            value='custom'
                            checked={formData.scopeMethod === 'custom'}
                            onChange={e =>
                              setFormData(prev => ({ ...prev, scopeMethod: e.target.value as 'current' | 'custom' }))
                            }
                            className='rounded-full border-[color:var(--jarvis-input-border)] text-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)] bg-transparent'
                          />
                        </div>
                        <div className='flex-1'>
                          <div
                            className={`text-sm font-medium ${formData.scopeMethod === 'custom' ? 'text-[var(--jarvis-primary)]' : 'text-[var(--jarvis-text-strong)]'}`}
                          >
                            Upload custom scopes (JSON)
                          </div>
                          <div className='text-xs text-[var(--jarvis-muted)] mt-1'>
                            Specify custom scopes in JSON format
                          </div>
                        </div>
                      </label>
                    </div>

                    {/* Custom Scopes JSON Input */}
                    {formData.scopeMethod === 'custom' && (
                      <div className='mt-3'>
                        <label
                          htmlFor='customScopes'
                          className='mb-1 block text-sm font-medium text-[var(--jarvis-text)]'
                        >
                          Custom Scopes (JSON format)
                        </label>
                        <textarea
                          id='customScopes'
                          className={`input h-24 font-mono text-xs ${
                            scopeValidationError
                              ? '!border-[var(--jarvis-danger)] focus:!border-[var(--jarvis-danger)] focus:!ring-[var(--jarvis-danger)]'
                              : ''
                          }`}
                          placeholder={`["mcp-servers-restricted/read", "mcp-registry-user"]`}
                          value={formData.customScopes}
                          onChange={e => setFormData(prev => ({ ...prev, customScopes: e.target.value }))}
                        />
                        <p className='mt-1 text-xs text-[var(--jarvis-muted)]'>
                          Enter a JSON array of scope names. Must be a subset of the scopes available for tokens.
                        </p>
                        {scopeValidationError && (
                          <p className='mt-1 text-xs text-[var(--jarvis-danger-text)]'>{scopeValidationError}</p>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Token Purpose */}
                  <div>
                    <h4 className='mb-2 text-sm font-semibold text-[var(--jarvis-text-strong)]'>Token Purpose</h4>
                    <div className='space-y-3'>
                      <label
                        className={`flex items-start space-x-3 p-3 rounded-xl border cursor-pointer transition-all duration-200 ${
                          formData.tokenPurpose === TokenPurpose.Interactive
                            ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary)]/5 ring-1 ring-[var(--jarvis-primary)]/20'
                            : 'border-[var(--jarvis-input-border)] hover:border-[var(--jarvis-primary)]/50 hover:bg-[var(--jarvis-card-muted)]'
                        }`}
                      >
                        <div className='mt-0.5 flex-shrink-0'>
                          <input
                            type='radio'
                            name='tokenPurpose'
                            value={TokenPurpose.Interactive}
                            checked={formData.tokenPurpose === TokenPurpose.Interactive}
                            onChange={() => setFormData(prev => ({ ...prev, tokenPurpose: TokenPurpose.Interactive }))}
                            className='rounded-full border-[color:var(--jarvis-input-border)] text-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)] bg-transparent'
                          />
                        </div>
                        <div className='flex-1'>
                          <div
                            className={`text-sm font-medium ${formData.tokenPurpose === TokenPurpose.Interactive ? 'text-[var(--jarvis-primary)]' : 'text-[var(--jarvis-text-strong)]'}`}
                          >
                            Interactive (default)
                          </div>
                          <div className='text-xs text-[var(--jarvis-muted)] mt-1'>
                            For use with an AI coding agent or other tool where you're present to approve access the
                            first time it calls a given MCP server.
                          </div>
                        </div>
                      </label>

                      <label
                        className={`flex items-start space-x-3 p-3 rounded-xl border cursor-pointer transition-all duration-200 ${
                          formData.tokenPurpose === TokenPurpose.Agent
                            ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary)]/5 ring-1 ring-[var(--jarvis-primary)]/20'
                            : 'border-[var(--jarvis-input-border)] hover:border-[var(--jarvis-primary)]/50 hover:bg-[var(--jarvis-card-muted)]'
                        }`}
                      >
                        <div className='mt-0.5 flex-shrink-0'>
                          <input
                            type='radio'
                            name='tokenPurpose'
                            value={TokenPurpose.Agent}
                            checked={formData.tokenPurpose === TokenPurpose.Agent}
                            onChange={() => setFormData(prev => ({ ...prev, tokenPurpose: TokenPurpose.Agent }))}
                            className='rounded-full border-[color:var(--jarvis-input-border)] text-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)] bg-transparent'
                          />
                        </div>
                        <div className='flex-1'>
                          <div
                            className={`text-sm font-medium ${formData.tokenPurpose === TokenPurpose.Agent ? 'text-[var(--jarvis-primary)]' : 'text-[var(--jarvis-text-strong)]'}`}
                          >
                            Non-interactive agent
                          </div>
                          <div className='text-xs text-[var(--jarvis-muted)] mt-1'>
                            For a headless agent (e.g. deployed to AWS AgentCore Runtime) with no one present to approve
                            access. Skips the per-server consent prompt.
                          </div>
                        </div>
                      </label>
                    </div>

                    {/* Security Warning for Agent Token */}
                    {formData.tokenPurpose === TokenPurpose.Agent && (
                      <div className='mt-3 flex items-start space-x-2 rounded-lg border border-[var(--jarvis-warning)]/30 bg-[var(--jarvis-warning-soft)] p-3 shadow-sm'>
                        <ExclamationTriangleIcon className='h-4 w-4 text-[var(--jarvis-warning-text)] flex-shrink-0 mt-0.5' />
                        <p className='text-xs text-[var(--jarvis-warning-text)] leading-relaxed'>
                          This token will call MCP servers without a one-time consent prompt. Only use it for agents you
                          control.
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Submit Button */}
              <button
                type='submit'
                disabled={loading || scopeValidationError !== null}
                className='w-full btn-primary flex items-center justify-center space-x-2 disabled:opacity-50 disabled:cursor-not-allowed py-2 text-sm'
              >
                {loading ? (
                  <>
                    <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-white'></div>
                    <span>Generating...</span>
                  </>
                ) : (
                  <>
                    <KeyIcon className='h-4 w-4' />
                    <span>Generate Token</span>
                  </>
                )}
              </button>

              {/* Error Display */}
              {error && (
                <div
                  role='alert'
                  className='rounded-lg border border-[var(--jarvis-danger)]/30 bg-[var(--jarvis-danger-soft)] p-3'
                >
                  <div className='flex items-center space-x-2'>
                    <ExclamationTriangleIcon className='h-4 w-4 text-[var(--jarvis-danger-text)]' />
                    <span className='text-sm text-[var(--jarvis-danger-text)]'>{error}</span>
                  </div>
                </div>
              )}
            </form>
          </div>

          {/* Generated Token Result */}
          {generatedToken && tokenDetails && (
            <div className='card border-[var(--jarvis-success)]/25 bg-[var(--jarvis-success-soft)] p-4'>
              <div className='flex items-center space-x-2 mb-3'>
                <CheckIcon className='h-5 w-5 text-[var(--jarvis-success-text)]' />
                <h3 className='text-lg font-semibold text-[var(--jarvis-success-text)]'>
                  Token Generated Successfully
                </h3>
              </div>

              {/* Token Display */}
              <div className='relative mb-4'>
                <div className='rounded-lg border border-[var(--jarvis-success)]/25 bg-[var(--jarvis-card)] p-4 pr-12'>
                  <code className='break-all text-sm font-mono text-[var(--jarvis-text)]'>{generatedToken}</code>
                </div>

                <div className='absolute right-2 top-2 z-10'>
                  <IconButton
                    ariaLabel='Copy token'
                    tooltip={copiedTarget === 'token' ? 'Copied!' : 'Copy token'}
                    onClick={() => handleCopy(generatedToken, 'token')}
                    size='card'
                    className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-icon-hover)] border-none bg-transparent hover:bg-transparent shadow-none'
                  >
                    {copiedTarget === 'token' ? (
                      <CheckIcon className='h-4 w-4 text-[var(--jarvis-success-text)]' />
                    ) : (
                      <ClipboardIcon className='h-4 w-4' />
                    )}
                  </IconButton>
                </div>
              </div>

              {/* CLI Helper */}
              <div className='mb-4'>
                <h4 className='mb-2 text-sm font-semibold text-[var(--jarvis-text-strong)]'>CLI Helper</h4>
                <div className='relative'>
                  <div className='rounded-lg border border-[var(--jarvis-success)]/25 bg-[var(--jarvis-card)] p-4 pr-12'>
                    <code className='break-all text-sm font-mono text-[var(--jarvis-text)]'>{cliHelper}</code>
                  </div>
                  <div className='absolute right-2 top-2 z-10'>
                    <IconButton
                      ariaLabel='Copy CLI helper'
                      tooltip={copiedTarget === 'cli-helper' ? 'Copied!' : 'Copy CLI helper'}
                      onClick={() => handleCopy(cliHelper, 'cli-helper')}
                      size='card'
                      className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-icon-hover)] border-none bg-transparent hover:bg-transparent shadow-none'
                    >
                      {copiedTarget === 'cli-helper' ? (
                        <CheckIcon className='h-4 w-4 text-[var(--jarvis-success-text)]' />
                      ) : (
                        <ClipboardIcon className='h-4 w-4' />
                      )}
                    </IconButton>
                  </div>
                </div>
              </div>

              {/* Token Details */}
              <div className='mb-4 space-y-2 text-sm text-[var(--jarvis-text)]'>
                <p>
                  <strong>Expires:</strong>{' '}
                  {new Date(Date.now() + tokenDetails.tokenData.expiresIn * 1000).toLocaleString()}
                </p>
                <p>
                  <strong>Scopes:</strong> {tokenDetails.tokenData.scope.split(' ').filter(Boolean).join(', ')}
                </p>
              </div>

              {/* Usage Instructions */}
              <div className='mb-4 rounded-lg border border-[var(--jarvis-info-text)]/25 bg-[var(--jarvis-info-soft)] p-4'>
                <h4 className='mb-2 text-sm font-semibold text-[var(--jarvis-info-text)]'>📋 Usage Instructions</h4>
                <p className='mb-2 text-sm text-[var(--jarvis-info-text)]'>Use this token in your API requests:</p>
                <code className='block rounded bg-[var(--jarvis-card)] p-2 text-sm font-mono text-[var(--jarvis-info-text)]'>
                  Authorization: Bearer YOUR_TOKEN_HERE
                </code>
                <p className='mt-2 text-xs text-[var(--jarvis-info-text)]'>
                  Replace YOUR_TOKEN_HERE with the token above.
                </p>
              </div>

              {/* Security Warning */}
              <div className='rounded-lg border border-[var(--jarvis-warning)]/25 bg-[var(--jarvis-warning-soft)] p-4'>
                <p className='text-sm text-[var(--jarvis-warning-text)]'>
                  <strong>⚠️ Important:</strong> This token will not be shown again. Save it securely!
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default TokenGeneration;
