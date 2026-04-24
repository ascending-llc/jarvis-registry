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
      <div className="mb-6">
        <label className="block text-sm font-medium text-[var(--jarvis-text)] mb-2">
          {label} {required && <span className="text-[var(--jarvis-danger-text)]">*</span>}
        </label>
        <input
          type={type}
          value={formData[field] as string}
          onChange={e => updateField(field, e.target.value)}
          disabled={isReadOnly}
          className={`w-full px-4 py-2 border rounded-md shadow-sm text-sm disabled:opacity-50 disabled:bg-[var(--jarvis-card-muted)] disabled:cursor-not-allowed disabled:bg-[var(--jarvis-card-muted)] bg-[var(--jarvis-card)] text-[var(--jarvis-text-strong)] transition-colors
 ${
 errors[field]
 ? 'border-[color:var(--jarvis-danger-soft)] focus:ring-[var(--jarvis-danger)] focus:border-[var(--jarvis-danger)]'
 : 'border-[color:var(--jarvis-border)] focus:ring-[var(--jarvis-primary)] focus:border-[var(--jarvis-primary)]'
 }`}
          placeholder={placeholder}
        />
        {errors[field] && <p className="mt-1 text-sm text-[var(--jarvis-danger-text)]">{errors[field]}</p>}
      </div>
    );
  };

  return (
    <div className="w-full text-left">
      {/* Provider Selection */}
      {!isEditMode && !isReadOnly && (
        <div className="mb-8">
          <label className="block text-sm font-medium text-[var(--jarvis-text)] mb-3">
            Provider Type <span className="text-[var(--jarvis-danger-text)]">*</span>
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div
              className={`h-full flex flex-col items-center justify-center p-5 rounded-[10px] border-2 cursor-pointer transition-all duration-150 text-center ${
 isAws
 ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)] '
 : 'border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] hover:border-[color:var(--jarvis-border)] hover:border-[color:var(--jarvis-border)] hover:bg-[var(--jarvis-card-muted)]'
 }`}
              onClick={() => updateField('providerType', 'aws_agentcore')}
            >
              <div className="w-12 h-12 rounded-[10px] flex items-center justify-center mx-auto mb-2.5 bg-[var(--jarvis-warning-soft)]  text-[var(--jarvis-warning-text)]">
                <FaAws className="w-7 h-7" />
              </div>
              <div
                className={`text-[15px] font-semibold ${isAws ? 'text-[var(--jarvis-primary)]' : 'text-[var(--jarvis-text-strong)] text-[var(--jarvis-text)]'}`}
              >
                AWS AgentCore
              </div>
              <div className="text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-text)] mt-1">
                Discover agents and MCP servers from Amazon AgentCore
              </div>
            </div>

            <div
              className={`h-full flex flex-col items-center justify-center p-5 rounded-[10px] border-2 cursor-pointer transition-all duration-150 text-center ${
 isAzure
 ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)] '
 : 'border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] hover:border-[color:var(--jarvis-border)] hover:border-[color:var(--jarvis-border)] hover:bg-[var(--jarvis-card-muted)]'
 }`}
              onClick={() => updateField('providerType', 'azure_ai_foundry')}
            >
              <div className="w-12 h-12 rounded-[10px] flex items-center justify-center mx-auto mb-2.5 bg-[var(--jarvis-info-soft)]  text-[var(--jarvis-info-text)]">
                <FaMicrosoft className="w-6 h-6" />
              </div>
              <div
                className={`text-[15px] font-semibold ${isAzure ? 'text-[var(--jarvis-primary)]' : 'text-[var(--jarvis-text-strong)] text-[var(--jarvis-text)]'}`}
              >
                Azure AI Foundry
              </div>
              <div className="text-xs text-[var(--jarvis-muted)] text-[var(--jarvis-text)] mt-1">
                Discover agents and MCP servers from Azure AI Foundry
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Basic Settings */}
      <h3 className="text-lg font-medium text-[var(--jarvis-text-strong)] mb-4">Basic Settings</h3>
      {renderInput('Display Name', 'displayName', 'e.g., Production AWS Account', 'text', true)}
      {renderInput('Description', 'description', 'Optional description of this provider connection')}

      <hr className="border-[color:var(--jarvis-border)] my-8" />

      {/* Provider Details */}
      <h3 className="text-lg font-medium text-[var(--jarvis-text-strong)] mb-4">Connection Settings</h3>

      {isAws && (
        <>
          <div className="mb-6 p-4 bg-[var(--jarvis-primary-soft)] rounded-lg border border-[var(--jarvis-primary-soft)] text-sm text-[var(--jarvis-primary-text)] text-[var(--jarvis-primary)]">
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
          <div className="mb-6 p-4 bg-[var(--jarvis-primary-soft)] rounded-lg border border-[var(--jarvis-primary-soft)] text-sm text-[var(--jarvis-primary-text)] text-[var(--jarvis-primary)]">
            Jarvis will authenticate using the managed identity to discover agents and MCP servers.
          </div>
          {renderInput(isEditMode || isReadOnly ? 'Azure Region' : 'Region', 'region', 'e.g., eastus', 'text', true)}
          {renderInput('Tenant ID', 'azureTenantId', 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', 'text', true)}
          {renderInput('Subscription ID', 'azureSubscriptionId', 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', 'text', true)}
          {renderInput('Resource Group', 'azureResourceGroup', 'e.g., rg-ai-foundry-prod', 'text', true)}
        </>
      )}

      <div className="mb-6">
        <label className="block text-sm font-medium text-[var(--jarvis-text)] mb-2">Resource Tags Filter</label>
        <input
          type='text'
          value={formData.resourceTagsFilter}
          onChange={e => updateField('resourceTagsFilter', e.target.value)}
          disabled={isReadOnly}
          className={`w-full px-4 py-2 border border-[color:var(--jarvis-border)] rounded-md shadow-sm text-sm disabled:opacity-50 disabled:bg-[var(--jarvis-card-muted)] disabled:cursor-not-allowed disabled:bg-[var(--jarvis-card-muted)] bg-[var(--jarvis-card)] text-[var(--jarvis-text-strong)] focus:ring-[var(--jarvis-primary)] focus:border-[var(--jarvis-primary)]`}
          placeholder='e.g., env:production, team:platform'
        />
        <p className="mt-1 text-xs text-[var(--jarvis-muted)]">
          Optional. Only import resources matching these tags. Comma-separated key:value pairs.
        </p>
      </div>

      {isEditMode && !isReadOnly && onTestConnection && (
        <div className="mb-6 p-4 bg-[var(--jarvis-bg)] bg-[var(--jarvis-card)]/50 rounded-lg border border-[color:var(--jarvis-border)]">
          <div className="flex items-center gap-3">
            <button
              onClick={onTestConnection}
              disabled={testConnectionLoading}
              className="shrink-0 inline-flex items-center gap-2 px-4 py-2 border border-[color:var(--jarvis-border)] rounded-md shadow-sm text-sm font-medium text-[var(--jarvis-text)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-card-muted)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--jarvis-primary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {testConnectionLoading ? (
                <>
                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-[var(--jarvis-primary)]"></div>
                  Testing...
                </>
              ) : (
                <>
                  <CheckCircleIcon className="h-4 w-4 text-[var(--jarvis-subtle)]" />
                  Test Connection
                </>
              )}
            </button>
            {testConnectionLoading ? (
              <span className="text-sm text-[var(--jarvis-muted)]">Assuming role and connecting...</span>
            ) : testConnectionResult ? (
              <span
                className={`text-sm ${
 testConnectionResult.success
 ? 'text-[var(--jarvis-success-text)]'
 : 'text-[var(--jarvis-danger-text)]'
 }`}
              >
                {testConnectionResult.message}
              </span>
            ) : (
              <span className="text-sm text-[var(--jarvis-subtle)]">Not tested yet</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default MainConfigForm;
