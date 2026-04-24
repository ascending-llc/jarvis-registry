import { CheckIcon, ClipboardIcon, ExclamationTriangleIcon, KeyIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useState } from 'react';

import SERVICES from '@/services';
import { useAuth } from '../contexts/AuthContext';
import IconButton from '@/components/IconButton';

const TokenGeneration: React.FC = () => {
  const { user } = useAuth();
  const [formData, setFormData] = useState({
    description: '',
    expiresInHours: 8,
    scopeMethod: 'current' as 'current' | 'custom',
    customScopes: '',
  });
  const [generatedToken, setGeneratedToken] = useState<string>('');
  const [tokenDetails, setTokenDetails] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string>('');

  const expirationOptions = [
    { value: 1, label: '1 hour' },
    { value: 8, label: '8 hours' },
    { value: 24, label: '24 hours' },
  ];

  const handleGenerateToken = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      const requestData: any = {
        description: formData.description,
        expiresInHours: formData.expiresInHours,
      };

      // Handle scopes based on the selected method
      if (formData.scopeMethod === 'custom') {
        const customScopesText = formData.customScopes.trim();
        if (customScopesText) {
          try {
            const parsedScopes = JSON.parse(customScopesText);
            if (!Array.isArray(parsedScopes)) {
              throw new Error('Custom scopes must be a JSON array');
            }
            requestData.requestedScopes = parsedScopes;
          } catch (_e) {
            setError('Invalid JSON format for custom scopes. Please provide a valid JSON array.');
            return;
          }
        }
      }
      // If using current scopes, we don't need to set requestedScopes - it will default to user's current scopes
      const response = await SERVICES.AUTH.getToken(requestData);

      if (response.success) {
        setGeneratedToken(response.tokenData.accessToken);
        setTokenDetails(response);
      } else {
        throw new Error('Token generation failed');
      }
    } catch (error: any) {
      console.error('Failed to generate token:', error);
      setError(error.response?.data?.detail || 'Failed to generate token');
    } finally {
      setLoading(false);
    }
  };

  const handleCopyToken = async () => {
    try {
      await navigator.clipboard.writeText(generatedToken);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (_error) {
      // Fallback for older browsers
      const textArea = document.createElement('textarea');
      textArea.value = generatedToken;
      textArea.style.position = 'fixed';
      textArea.style.left = '-999999px';
      textArea.style.top = '-999999px';
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();

      try {
        document.execCommand('copy');
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch (err) {
        console.error('Failed to copy token:', err);
      }

      document.body.removeChild(textArea);
    }
  };

  const validateCustomScopes = () => {
    if (formData.scopeMethod === 'custom' && formData.customScopes.trim()) {
      try {
        const parsed = JSON.parse(formData.customScopes);
        if (!Array.isArray(parsed)) {
          return 'Custom scopes must be a JSON array';
        }
        return null;
      } catch (_e) {
        return 'Invalid JSON format';
      }
    }
    return null;
  };

  const scopeValidationError = validateCustomScopes();

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
              <span className='text-xs font-medium text-[var(--jarvis-text)]'>Current Scopes:</span>
              <div className='flex flex-wrap gap-1 mt-1'>
                {user?.scopes && user.scopes.length > 0 ? (
                  user.scopes.map(scope => (
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
              <em>Generated tokens can have the same or fewer permissions than your current scopes.</em>
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
                    <label
                      htmlFor='description'
                      className='mb-1 block text-sm font-medium text-[var(--jarvis-text)]'
                    >
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
                <div className='space-y-3'>
                  {/* Scope Configuration */}
                  <div>
                    <h4 className='mb-2 text-sm font-semibold text-[var(--jarvis-text-strong)]'>Scope Configuration</h4>

                    <div className='space-y-2'>
                      <label className='flex items-center space-x-2'>
                        <input
                          type='radio'
                          name='scopeMethod'
                          value='current'
                          checked={formData.scopeMethod === 'current'}
                          onChange={e =>
                            setFormData(prev => ({ ...prev, scopeMethod: e.target.value as 'current' | 'custom' }))
                          }
                          className='rounded border-[color:var(--jarvis-input-border)] text-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)]'
                        />
                        <div>
                          <div className='text-sm font-medium text-[var(--jarvis-text)]'>Use my current scopes</div>
                          <div className='text-xs text-[var(--jarvis-muted)]'>
                            Generate token with all your current permissions
                          </div>
                        </div>
                      </label>

                      <label className='flex items-center space-x-2'>
                        <input
                          type='radio'
                          name='scopeMethod'
                          value='custom'
                          checked={formData.scopeMethod === 'custom'}
                          onChange={e =>
                            setFormData(prev => ({ ...prev, scopeMethod: e.target.value as 'current' | 'custom' }))
                          }
                          className='rounded border-[color:var(--jarvis-input-border)] text-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)]'
                        />
                        <div>
                          <div className='text-sm font-medium text-[var(--jarvis-text)]'>
                            Upload custom scopes (JSON)
                          </div>
                          <div className='text-xs text-[var(--jarvis-muted)]'>
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
                          Enter a JSON array of scope names. Must be a subset of your current scopes.
                        </p>
                        {scopeValidationError && (
                          <p className='mt-1 text-xs text-[var(--jarvis-danger-text)]'>{scopeValidationError}</p>
                        )}
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
                <div className='rounded-lg border border-[var(--jarvis-danger)]/30 bg-[var(--jarvis-danger-soft)] p-3'>
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
                <div className='rounded-lg border border-[var(--jarvis-success)]/25 bg-[var(--jarvis-card)] p-4'>
                  <code className='break-all text-sm font-mono text-[var(--jarvis-text)]'>{generatedToken}</code>
                </div>

                <div className="absolute right-2 top-2 z-10">
                  <IconButton
                    ariaLabel="Copy token"
                    tooltip={copied ? "Copied!" : "Copy token"}
                    onClick={handleCopyToken}
                    size="card"
                    className="text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-icon-hover)] border-none bg-transparent hover:bg-transparent shadow-none"
                  >
                    {copied ? (
                      <CheckIcon className='h-4 w-4 text-[var(--jarvis-success-text)]' />
                    ) : (
                      <ClipboardIcon className='h-4 w-4' />
                    )}
                  </IconButton>
                </div>
              </div>

              {/* Token Details */}
              <div className='mb-4 space-y-2 text-sm text-[var(--jarvis-text)]'>
                <p>
                  <strong>Expires:</strong>{' '}
                  {new Date(Date.now() + tokenDetails.tokenData.expiresIn * 1000).toLocaleString()}
                </p>
                <p>
                  <strong>Scopes:</strong> {tokenDetails.requestedScopes.join(', ')}
                </p>
                {tokenDetails.tokenData.description && (
                  <p>
                    <strong>Description:</strong> {tokenDetails.tokenData.description}
                  </p>
                )}
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
