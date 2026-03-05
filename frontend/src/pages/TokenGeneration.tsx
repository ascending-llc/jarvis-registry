import { CheckIcon, ClipboardIcon, ExclamationTriangleIcon, KeyIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useState } from 'react';

import SERVICES from '@/services';
import { useAuth } from '../contexts/AuthContext';

const TokenGeneration: React.FC = () => {
  const { user } = useAuth();
  const [formData, setFormData] = useState({
    description: '',
    expires_in_hours: 8,
    scope_method: 'current' as 'current' | 'custom',
    custom_scopes: '',
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
        expires_in_hours: formData.expires_in_hours,
      };

      // Handle scopes based on the selected method
      if (formData.scope_method === 'custom') {
        const customScopesText = formData.custom_scopes.trim();
        if (customScopesText) {
          try {
            const parsedScopes = JSON.parse(customScopesText);
            if (!Array.isArray(parsedScopes)) {
              throw new Error('Custom scopes must be a JSON array');
            }
            requestData.requested_scopes = parsedScopes;
          } catch (_e) {
            setError('Invalid JSON format for custom scopes. Please provide a valid JSON array.');
            return;
          }
        }
      }
      // If using current scopes, we don't need to set requested_scopes - it will default to user's current scopes
      const response = await SERVICES.AUTH.getToken(requestData);

      if (response.success) {
        setGeneratedToken(response.token_data.access_token);
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
    if (formData.scope_method === 'custom' && formData.custom_scopes.trim()) {
      try {
        const parsed = JSON.parse(formData.custom_scopes);
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
          <div className='mx-auto w-10 h-10 bg-primary-100 dark:bg-primary-900 rounded-full flex items-center justify-center mb-2'>
            <KeyIcon className='w-5 h-5 text-primary-600 dark:text-primary-400' />
          </div>
          <h1 className='text-xl font-bold text-gray-900 dark:text-white'>Generate JWT Token</h1>
          <p className='text-sm text-gray-600 dark:text-gray-400'>
            Generate a personal access token for programmatic access to MCP servers
          </p>
        </div>
      </div>

      {/* Scrollable Content Area */}
      <div className='flex-1 overflow-y-auto min-h-0'>
        <div className='max-w-4xl mx-auto space-y-4 pb-6'>
          {/* Current User Permissions - Compact */}
          <div className='card p-4 bg-gray-50 dark:bg-gray-800'>
            <h3 className='text-base font-semibold text-gray-900 dark:text-white mb-2'>Your Current Permissions</h3>
            <div className='mb-2'>
              <span className='text-xs font-medium text-gray-700 dark:text-gray-300'>Current Scopes:</span>
              <div className='flex flex-wrap gap-1 mt-1'>
                {user?.scopes && user.scopes.length > 0 ? (
                  user.scopes.map(scope => (
                    <span
                      key={scope}
                      className='inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200'
                    >
                      {scope}
                    </span>
                  ))
                ) : (
                  <span className='text-xs text-gray-500 dark:text-gray-400'>No scopes available</span>
                )}
              </div>
            </div>
            <p className='text-xs text-gray-600 dark:text-gray-400'>
              <em>Generated tokens can have the same or fewer permissions than your current scopes.</em>
            </p>
          </div>

          {/* Token Configuration Form */}
          <div className='card p-4'>
            <form onSubmit={handleGenerateToken} className='space-y-4'>
              <h3 className='text-base font-semibold text-gray-900 dark:text-white'>Token Configuration</h3>

              {/* Form Fields - Responsive Grid */}
              <div className='grid grid-cols-1 lg:grid-cols-2 gap-4'>
                {/* Left Column */}
                <div className='space-y-3'>
                  {/* Description */}
                  <div>
                    <label
                      htmlFor='description'
                      className='block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'
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
                      htmlFor='expires_in_hours'
                      className='block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'
                    >
                      Expires In
                    </label>
                    <select
                      id='expires_in_hours'
                      className='input text-sm'
                      value={formData.expires_in_hours}
                      onChange={e => setFormData(prev => ({ ...prev, expires_in_hours: parseInt(e.target.value, 10) }))}
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
                    <h4 className='text-sm font-semibold text-gray-900 dark:text-white mb-2'>Scope Configuration</h4>

                    <div className='space-y-2'>
                      <label className='flex items-center space-x-2'>
                        <input
                          type='radio'
                          name='scope_method'
                          value='current'
                          checked={formData.scope_method === 'current'}
                          onChange={e =>
                            setFormData(prev => ({ ...prev, scope_method: e.target.value as 'current' | 'custom' }))
                          }
                          className='rounded border-gray-300 text-primary-600 focus:ring-primary-500'
                        />
                        <div>
                          <div className='text-sm font-medium text-gray-900 dark:text-white'>Use my current scopes</div>
                          <div className='text-xs text-gray-500 dark:text-gray-400'>
                            Generate token with all your current permissions
                          </div>
                        </div>
                      </label>

                      <label className='flex items-center space-x-2'>
                        <input
                          type='radio'
                          name='scope_method'
                          value='custom'
                          checked={formData.scope_method === 'custom'}
                          onChange={e =>
                            setFormData(prev => ({ ...prev, scope_method: e.target.value as 'current' | 'custom' }))
                          }
                          className='rounded border-gray-300 text-primary-600 focus:ring-primary-500'
                        />
                        <div>
                          <div className='text-sm font-medium text-gray-900 dark:text-white'>
                            Upload custom scopes (JSON)
                          </div>
                          <div className='text-xs text-gray-500 dark:text-gray-400'>
                            Specify custom scopes in JSON format
                          </div>
                        </div>
                      </label>
                    </div>

                    {/* Custom Scopes JSON Input */}
                    {formData.scope_method === 'custom' && (
                      <div className='mt-3'>
                        <label
                          htmlFor='custom_scopes'
                          className='block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'
                        >
                          Custom Scopes (JSON format)
                        </label>
                        <textarea
                          id='custom_scopes'
                          className={`input h-24 font-mono text-xs ${scopeValidationError ? 'border-red-300 focus:border-red-500 focus:ring-red-500' : ''}`}
                          placeholder={`["mcp-servers-restricted/read", "mcp-registry-user"]`}
                          value={formData.custom_scopes}
                          onChange={e => setFormData(prev => ({ ...prev, custom_scopes: e.target.value }))}
                        />
                        <p className='mt-1 text-xs text-gray-500 dark:text-gray-400'>
                          Enter a JSON array of scope names. Must be a subset of your current scopes.
                        </p>
                        {scopeValidationError && (
                          <p className='mt-1 text-xs text-red-600 dark:text-red-400'>{scopeValidationError}</p>
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
                <div className='p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg'>
                  <div className='flex items-center space-x-2'>
                    <ExclamationTriangleIcon className='h-4 w-4 text-red-600 dark:text-red-400' />
                    <span className='text-sm text-red-800 dark:text-red-200'>{error}</span>
                  </div>
                </div>
              )}
            </form>
          </div>

          {/* Generated Token Result */}
          {generatedToken && tokenDetails && (
            <div className='card p-4 bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'>
              <div className='flex items-center space-x-2 mb-3'>
                <CheckIcon className='h-5 w-5 text-green-600 dark:text-green-400' />
                <h3 className='text-lg font-semibold text-green-900 dark:text-green-100'>
                  Token Generated Successfully
                </h3>
              </div>

              {/* Token Display */}
              <div className='relative mb-4'>
                <div className='bg-white dark:bg-gray-800 p-4 rounded-lg border border-green-200 dark:border-green-700'>
                  <code className='text-sm font-mono break-all text-gray-900 dark:text-gray-100'>{generatedToken}</code>
                </div>

                <button
                  onClick={handleCopyToken}
                  className='absolute top-2 right-2 p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded hover:bg-gray-100 dark:hover:bg-gray-700'
                  title={copied ? 'Copied!' : 'Copy token'}
                >
                  {copied ? <CheckIcon className='h-4 w-4 text-green-600' /> : <ClipboardIcon className='h-4 w-4' />}
                </button>
              </div>

              {/* Token Details */}
              <div className='space-y-2 text-sm mb-4 text-gray-900 dark:text-white'>
                <p>
                  <strong>Expires:</strong>{' '}
                  {new Date(Date.now() + tokenDetails.token_data.expires_in * 1000).toLocaleString()}
                </p>
                <p>
                  <strong>Scopes:</strong> {tokenDetails.requested_scopes.join(', ')}
                </p>
                {tokenDetails.token_data.description && (
                  <p>
                    <strong>Description:</strong> {tokenDetails.token_data.description}
                  </p>
                )}
              </div>

              {/* Usage Instructions */}
              <div className='p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg mb-4'>
                <h4 className='text-sm font-semibold text-blue-900 dark:text-blue-100 mb-2'>📋 Usage Instructions</h4>
                <p className='text-sm text-blue-800 dark:text-blue-200 mb-2'>Use this token in your API requests:</p>
                <code className='block text-sm bg-blue-100 dark:bg-blue-900/40 p-2 rounded font-mono text-blue-900 dark:text-blue-100'>
                  Authorization: Bearer YOUR_TOKEN_HERE
                </code>
                <p className='text-xs text-blue-600 dark:text-blue-300 mt-2'>
                  Replace YOUR_TOKEN_HERE with the token above.
                </p>
              </div>

              {/* Security Warning */}
              <div className='p-4 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg'>
                <p className='text-sm text-yellow-800 dark:text-yellow-200'>
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
