import { CheckCircleIcon } from '@heroicons/react/24/solid';
import type React from 'react';
import { FaAws, FaMicrosoft } from 'react-icons/fa';

import type { FederationFormConfig } from './types';

interface MainConfigFormProps {
  formData: FederationFormConfig;
  updateField: (field: keyof FederationFormConfig, value: any) => void;
  errors: Record<string, string | undefined>;
  isEditMode: boolean;
  isReadOnly: boolean;
  onTestConnection?: () => void;
  testConnectionLoading?: boolean;
  testConnectionResult?: { success: boolean; message: string } | null;
}

const MainConfigForm: React.FC<MainConfigFormProps> = ({
  formData,
  updateField,
  errors,
  isEditMode,
  isReadOnly,
  onTestConnection,
  testConnectionLoading = false,
  testConnectionResult,
}) => {
  const isAws = formData.providerType === 'aws_agentcore';
  const isAzure = formData.providerType === 'azure_ai_foundry';

  const renderInput = (
    label: string,
    field: keyof FederationFormConfig,
    placeholder: string,
    type = 'text',
    required = false,
  ) => {
    return (
      <div className='mb-6'>
        <label className='block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2'>
          {label} {required && <span className='text-red-500'>*</span>}
        </label>
        <input
          type={type}
          value={formData[field] as string}
          onChange={e => updateField(field, e.target.value)}
          disabled={isReadOnly}
          className={`w-full px-4 py-2 border rounded-md shadow-sm text-sm disabled:opacity-50 disabled:bg-gray-100 disabled:cursor-not-allowed dark:disabled:bg-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white transition-colors
            ${
              errors[field]
                ? 'border-red-300 focus:ring-red-500 focus:border-red-500'
                : 'border-gray-300 dark:border-gray-600 focus:ring-purple-500 focus:border-purple-500'
            }`}
          placeholder={placeholder}
        />
        {errors[field] && <p className='mt-1 text-sm text-red-600 dark:text-red-400'>{errors[field]}</p>}
      </div>
    );
  };

  return (
    <div className='w-full text-left'>
      {/* Provider Selection */}
      {!isEditMode && !isReadOnly && (
        <div className='mb-8'>
          <label className='block text-sm font-medium text-gray-700 dark:text-gray-300 mb-3'>
            Provider Type <span className='text-red-500'>*</span>
          </label>
          <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
            <div
              className={`h-full flex flex-col items-center justify-center p-5 rounded-[10px] border-2 cursor-pointer transition-all duration-150 text-center ${
                isAws
                  ? 'border-purple-600 dark:border-purple-500 bg-purple-50 dark:bg-purple-600/10'
                  : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700'
              }`}
              onClick={() => updateField('providerType', 'aws_agentcore')}
            >
              <div className='w-12 h-12 rounded-[10px] flex items-center justify-center mx-auto mb-2.5 bg-amber-100 dark:bg-amber-500/15 text-amber-500'>
                <FaAws className='w-7 h-7' />
              </div>
              <div
                className={`text-[15px] font-semibold ${isAws ? 'text-purple-700 dark:text-purple-400' : 'text-gray-900 dark:text-gray-200'}`}
              >
                AWS AgentCore
              </div>
              <div className='text-xs text-gray-500 dark:text-gray-300 mt-1'>
                Discover agents and MCP servers from Amazon AgentCore
              </div>
            </div>

            <div
              className={`h-full flex flex-col items-center justify-center p-5 rounded-[10px] border-2 cursor-pointer transition-all duration-150 text-center ${
                isAzure
                  ? 'border-purple-600 dark:border-purple-500 bg-purple-50 dark:bg-purple-600/10'
                  : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700'
              }`}
              onClick={() => updateField('providerType', 'azure_ai_foundry')}
            >
              <div className='w-12 h-12 rounded-[10px] flex items-center justify-center mx-auto mb-2.5 bg-blue-100 dark:bg-blue-500/15 text-blue-500'>
                <FaMicrosoft className='w-6 h-6' />
              </div>
              <div
                className={`text-[15px] font-semibold ${isAzure ? 'text-purple-700 dark:text-purple-400' : 'text-gray-900 dark:text-gray-200'}`}
              >
                Azure AI Foundry
              </div>
              <div className='text-xs text-gray-500 dark:text-gray-300 mt-1'>
                Discover agents and MCP servers from Azure AI Foundry
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Basic Settings */}
      <h3 className='text-lg font-medium text-gray-900 dark:text-white mb-4'>Basic Settings</h3>
      {renderInput('Display Name', 'displayName', 'e.g., Production AWS Account', 'text', true)}
      {renderInput('Description', 'description', 'Optional description of this provider connection')}

      <hr className='border-gray-200 dark:border-gray-700 my-8' />

      {/* Provider Details */}
      <h3 className='text-lg font-medium text-gray-900 dark:text-white mb-4'>Connection Settings</h3>

      {isAws && (
        <>
          <div className='mb-6 p-4 bg-purple-50 dark:bg-purple-900/20 rounded-lg border border-purple-100 dark:border-purple-800 text-sm text-purple-800 dark:text-purple-300'>
            The pod's execution role will assume the role below via STS to perform AgentCore control plane operations in
            the specified region.
          </div>
          {renderInput(isEditMode || isReadOnly ? 'AWS Region' : 'Region', 'region', 'e.g., us-east-1', 'text', true)}
          {renderInput(
            'AGENTCORE_ASSUME_ROLE_ARN',
            'assumeRoleArn',
            'arn:aws:iam::123456789012:role/JarvisRole',
            'text',
            true,
          )}
        </>
      )}

      {isAzure && (
        <>
          <div className='mb-6 p-4 bg-purple-50 dark:bg-purple-900/20 rounded-lg border border-purple-100 dark:border-purple-800 text-sm text-purple-800 dark:text-purple-300'>
            Jarvis will authenticate using the managed identity to discover agents and MCP servers.
          </div>
          {renderInput(isEditMode || isReadOnly ? 'Azure Region' : 'Region', 'region', 'e.g., eastus', 'text', true)}
          {renderInput('Tenant ID', 'azureTenantId', 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', 'text', true)}
          {renderInput('Subscription ID', 'azureSubscriptionId', 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', 'text', true)}
          {renderInput('Resource Group', 'azureResourceGroup', 'e.g., rg-ai-foundry-prod', 'text', true)}
        </>
      )}

      <div className='mb-6'>
        <label className='block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2'>Resource Tags Filter</label>
        <input
          type='text'
          value={formData.resourceTagsFilter}
          onChange={e => updateField('resourceTagsFilter', e.target.value)}
          disabled={isReadOnly}
          className={`w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm disabled:opacity-50 disabled:bg-gray-100 disabled:cursor-not-allowed dark:disabled:bg-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-purple-500 focus:border-purple-500`}
          placeholder='e.g., env:production, team:platform'
        />
        <p className='mt-1 text-xs text-gray-500 dark:text-gray-400'>
          Optional. Only import resources matching these tags. Comma-separated key:value pairs.
        </p>
      </div>

      {isEditMode && !isReadOnly && onTestConnection && (
        <div className='mb-6 p-4 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-gray-200 dark:border-gray-700'>
          <div className='flex items-center gap-3'>
            <button
              onClick={onTestConnection}
              disabled={testConnectionLoading}
              className='shrink-0 inline-flex items-center gap-2 px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm font-medium text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors'
            >
              {testConnectionLoading ? (
                <>
                  <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-purple-600'></div>
                  Testing...
                </>
              ) : (
                <>
                  <CheckCircleIcon className='h-4 w-4 text-gray-400' />
                  Test Connection
                </>
              )}
            </button>
            {testConnectionLoading ? (
              <span className='text-sm text-gray-500 dark:text-gray-400'>Assuming role and connecting...</span>
            ) : testConnectionResult ? (
              <span
                className={`text-sm ${
                  testConnectionResult.success
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : 'text-red-600 dark:text-red-400'
                }`}
              >
                {testConnectionResult.message}
              </span>
            ) : (
              <span className='text-sm text-gray-400'>Not tested yet</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default MainConfigForm;
