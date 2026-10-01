import { CheckCircleIcon, DocumentDuplicateIcon, PlusIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useState } from 'react';
import { FaAws, FaGithub, FaMicrosoft } from 'react-icons/fa';

import { InputField } from '@/components/FormFields';

import { DEFAULT_GITHUB_REF, normalizePaths, normalizeTags } from './formUtils';
import type { FederationFormConfig, FederationFormStringField } from './types';

interface MainConfigFormProps {
  formData: FederationFormConfig;
  updateField: <Field extends keyof FederationFormConfig>(field: Field, value: FederationFormConfig[Field]) => void;
  errors: Record<string, string | undefined>;
  isEditMode: boolean;
  isReadOnly: boolean;
  hasGithubClientSecret?: boolean;
  githubCallbackUrl: string;
  onCopyGithubCallbackUrl: () => void;
  onTestConnection?: () => void;
  testConnectionLoading?: boolean;
  testConnectionDisabled?: boolean;
  testConnectionDisabledReason?: string;
  testConnectionResult?: { success: boolean; message: string } | null;
}

const MainConfigForm: React.FC<MainConfigFormProps> = ({
  formData,
  updateField,
  errors,
  isEditMode,
  isReadOnly,
  hasGithubClientSecret = false,
  githubCallbackUrl,
  onCopyGithubCallbackUrl,
  onTestConnection,
  testConnectionLoading = false,
  testConnectionDisabled = false,
  testConnectionDisabledReason,
  testConnectionResult,
}) => {
  const [tagInput, setTagInput] = useState('');
  const isAws = formData.providerType === 'aws_agentcore';
  const isAzure = formData.providerType === 'azure_ai_foundry';
  const isGithub = formData.providerType === 'github';

  const renderInput = (
    label: string,
    field: FederationFormStringField,
    placeholder: string,
    type = 'text',
    required = false,
    helperText?: string,
  ) => (
    <div className='mb-6'>
      <label htmlFor={field} className='mb-2 block text-sm font-medium text-[var(--jarvis-text)]'>
        {label} {required && <span className='text-[var(--jarvis-danger-text)]'>*</span>}
      </label>
      <input
        id={field}
        type={type}
        value={formData[field]}
        onChange={event => updateField(field, event.target.value)}
        disabled={isReadOnly}
        className={`w-full rounded-md border bg-[var(--jarvis-card)] px-4 py-2 text-sm text-[var(--jarvis-text-strong)] shadow-sm transition-colors disabled:cursor-not-allowed disabled:bg-[var(--jarvis-card-muted)] disabled:opacity-50 ${
          errors[field]
            ? 'border-[color:var(--jarvis-danger-soft)] focus:border-[var(--jarvis-danger)] focus:ring-[var(--jarvis-danger)]'
            : 'border-[color:var(--jarvis-border)] focus:border-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)]'
        }`}
        placeholder={placeholder}
      />
      {helperText && <p className='mt-1 text-xs text-[var(--jarvis-muted)]'>{helperText}</p>}
      {errors[field] && <p className='mt-1 text-sm text-[var(--jarvis-danger-text)]'>{errors[field]}</p>}
    </div>
  );

  const addTag = () => {
    if (!tagInput.trim()) return;
    const nextTags = normalizeTags([...formData.tags, tagInput]);
    if (nextTags.length !== formData.tags.length) updateField('tags', nextTags);
    setTagInput('');
  };

  const updatePath = (index: number, value: string) => {
    updateField(
      'paths',
      formData.paths.map((path, pathIndex) => (pathIndex === index ? value : path)),
    );
  };

  const removePath = (index: number) => {
    updateField(
      'paths',
      formData.paths.filter((_, pathIndex) => pathIndex !== index),
    );
  };

  return (
    <div className='w-full text-left'>
      {!isEditMode && !isReadOnly && (
        <div className='mb-8'>
          <label className='mb-3 block text-sm font-medium text-[var(--jarvis-text)]'>
            Provider Type <span className='text-[var(--jarvis-danger-text)]'>*</span>
          </label>
          <div className='grid grid-cols-1 gap-3 sm:grid-cols-3'>
            <button
              type='button'
              className={`flex h-full flex-col items-center justify-center rounded-[10px] border-2 p-5 text-center transition-all duration-150 ${
                isAws
                  ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)]'
                  : 'border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-card-muted)]'
              }`}
              onClick={() => updateField('providerType', 'aws_agentcore')}
            >
              <div className='mx-auto mb-2.5 flex h-12 w-12 items-center justify-center rounded-[10px] bg-[var(--jarvis-warning-soft)] text-[var(--jarvis-warning-text)]'>
                <FaAws className='h-7 w-7' />
              </div>
              <div
                className={`text-[15px] font-semibold ${isAws ? 'text-[var(--jarvis-primary)]' : 'text-[var(--jarvis-text)]'}`}
              >
                AWS AgentCore
              </div>
              <div className='mt-1 text-xs text-[var(--jarvis-muted)]'>
                Discover agents and MCP servers from Amazon AgentCore
              </div>
            </button>

            <button
              type='button'
              className={`flex h-full flex-col items-center justify-center rounded-[10px] border-2 p-5 text-center transition-all duration-150 ${
                isAzure
                  ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)]'
                  : 'border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-card-muted)]'
              }`}
              onClick={() => updateField('providerType', 'azure_ai_foundry')}
            >
              <div className='mx-auto mb-2.5 flex h-12 w-12 items-center justify-center rounded-[10px] bg-[var(--jarvis-info-soft)] text-[var(--jarvis-info-text)]'>
                <FaMicrosoft className='h-6 w-6' />
              </div>
              <div
                className={`text-[15px] font-semibold ${isAzure ? 'text-[var(--jarvis-primary)]' : 'text-[var(--jarvis-text)]'}`}
              >
                Azure AI Foundry
              </div>
              <div className='mt-1 text-xs text-[var(--jarvis-muted)]'>
                Discover agents and MCP servers from Azure AI Foundry
              </div>
            </button>

            <button
              type='button'
              className={`flex h-full flex-col items-center justify-center rounded-[10px] border-2 p-5 text-center transition-all duration-150 ${
                isGithub
                  ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)]'
                  : 'border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-card-muted)]'
              }`}
              onClick={() => updateField('providerType', 'github')}
            >
              <div className='mx-auto mb-2.5 flex h-12 w-12 items-center justify-center rounded-[10px] bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)]'>
                <FaGithub className='h-7 w-7' />
              </div>
              <div
                className={`text-[15px] font-semibold ${isGithub ? 'text-[var(--jarvis-primary)]' : 'text-[var(--jarvis-text)]'}`}
              >
                GitHub
              </div>
              <div className='mt-1 text-xs text-[var(--jarvis-muted)]'>Sync skills from a GitHub repository</div>
            </button>
          </div>
        </div>
      )}

      <h3 className='mb-4 text-lg font-medium text-[var(--jarvis-text-strong)]'>Basic Settings</h3>
      {renderInput('Display Name', 'displayName', 'e.g., Production External Provider', 'text', true)}
      {renderInput('Description', 'description', 'Optional description of this provider connection')}

      {isGithub && (
        <div className='mb-6'>
          <label htmlFor='github-tag-input' className='mb-2 block text-sm font-medium text-[var(--jarvis-text)]'>
            Tags
          </label>
          <div className='flex min-h-10 flex-wrap items-center gap-2 rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-3 py-2 shadow-sm'>
            {formData.tags.map(tag => (
              <span
                key={tag}
                className='inline-flex items-center gap-1.5 rounded-md bg-[var(--jarvis-primary-soft)] px-2.5 py-1 text-xs font-semibold text-[var(--jarvis-primary-text)]'
              >
                {tag}
                {!isReadOnly && (
                  <button
                    type='button'
                    aria-label={`Remove ${tag} tag`}
                    onClick={() =>
                      updateField(
                        'tags',
                        formData.tags.filter(item => item !== tag),
                      )
                    }
                    className='text-[var(--jarvis-primary-text)] hover:text-[var(--jarvis-text-strong)]'
                  >
                    <XMarkIcon className='h-3.5 w-3.5' />
                  </button>
                )}
              </span>
            ))}
            {!isReadOnly && (
              <input
                id='github-tag-input'
                type='text'
                value={tagInput}
                onChange={event => setTagInput(event.target.value)}
                onKeyDown={event => {
                  if (event.key !== 'Enter') return;
                  event.preventDefault();
                  addTag();
                }}
                onBlur={addTag}
                placeholder='Add tag and press Enter'
                className='min-w-40 flex-1 border-0 bg-transparent px-1 py-1 text-sm text-[var(--jarvis-text)] outline-none placeholder:text-[var(--jarvis-input-placeholder)] focus:ring-0'
              />
            )}
          </div>
        </div>
      )}

      <hr className='my-8 border-[color:var(--jarvis-border)]' />

      <h3 className='mb-4 text-lg font-medium text-[var(--jarvis-text-strong)]'>Connection Settings</h3>

      {isAws && (
        <>
          <div className='mb-6 rounded-lg border border-[var(--jarvis-primary-soft)] bg-[var(--jarvis-primary-soft)] p-4 text-sm text-[var(--jarvis-primary-text)]'>
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
          <div className='mb-6 rounded-lg border border-[var(--jarvis-primary-soft)] bg-[var(--jarvis-primary-soft)] p-4 text-sm text-[var(--jarvis-primary-text)]'>
            Jarvis authenticates using the managed identity by default. To use a service principal instead, fill in
            Tenant ID, Client ID, and Client Secret together.
          </div>
          {renderInput(
            'Project Endpoint',
            'projectEndpoint',
            'https://{account}.services.ai.azure.com/api/projects/{project}',
            'text',
            true,
          )}
          {renderInput('Tenant ID', 'tenantId', 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx')}
          {renderInput('Client ID', 'clientId', 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx')}
          {renderInput('Client Secret', 'clientSecret', 'Service principal client secret', 'password')}
        </>
      )}

      {isGithub && (
        <>
          <div className='mb-6 rounded-lg border border-[var(--jarvis-primary-soft)] bg-[var(--jarvis-primary-soft)] p-4 text-sm text-[var(--jarvis-primary-text)]'>
            A GitHub App scoped to this repository reads skill files. Each configured path is treated as a container,
            and its direct child folders containing a SKILL.md are imported as skills.
          </div>

          <div className='mb-6'>
            <label htmlFor='github-callback-url' className='mb-2 block text-sm font-medium text-[var(--jarvis-text)]'>
              Callback URL
            </label>
            <div className='flex gap-2'>
              <input
                id='github-callback-url'
                type='text'
                value={githubCallbackUrl}
                readOnly
                className='min-w-0 flex-1 rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-4 py-2 font-mono text-sm text-[var(--jarvis-muted)] shadow-sm'
              />
              <button
                type='button'
                aria-label='Copy callback URL'
                onClick={onCopyGithubCallbackUrl}
                className='inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] text-[var(--jarvis-icon)] transition-colors hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-icon-hover)]'
              >
                <DocumentDuplicateIcon className='h-4 w-4' />
              </button>
            </div>
            <p className='mt-1 text-xs text-[var(--jarvis-muted)]'>
              Register this URL in the GitHub App so GitHub can redirect back to Jarvis after authorization.
            </p>
          </div>

          <div className='grid grid-cols-1 gap-x-5 sm:grid-cols-2'>
            {renderInput('Owner', 'owner', 'e.g., my-org', 'text', true)}
            {renderInput('Repo', 'repo', 'e.g., skills-repo', 'text', true)}
          </div>
          {renderInput(
            'Ref',
            'ref',
            DEFAULT_GITHUB_REF,
            'text',
            false,
            `Branch, tag, or commit SHA to sync from. Defaults to ${DEFAULT_GITHUB_REF} when left blank.`,
          )}

          <div className='mb-6'>
            <label className='mb-2 block text-sm font-medium text-[var(--jarvis-text)]'>
              Paths <span className='text-[var(--jarvis-danger-text)]'>*</span>
            </label>
            <div className='space-y-2'>
              {formData.paths.map((path, index) => (
                <div key={index} className='flex gap-2'>
                  <input
                    type='text'
                    aria-label={`Repository path ${index + 1}`}
                    value={path}
                    onChange={event => updatePath(index, event.target.value)}
                    onBlur={() => updateField('paths', normalizePaths(formData.paths))}
                    disabled={isReadOnly}
                    placeholder={index === 0 ? '.' : 'prompts/mcp'}
                    className='min-w-0 flex-1 rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-4 py-2 font-mono text-sm text-[var(--jarvis-text-strong)] shadow-sm focus:border-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:bg-[var(--jarvis-card-muted)] disabled:opacity-50'
                  />
                  {!isReadOnly && (
                    <button
                      type='button'
                      aria-label={`Remove repository path ${index + 1}`}
                      onClick={() => removePath(index)}
                      className='inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] text-[var(--jarvis-icon)] transition-colors hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-icon-hover)]'
                    >
                      <XMarkIcon className='h-4 w-4' />
                    </button>
                  )}
                </div>
              ))}
            </div>
            <p className='mt-1 text-xs text-[var(--jarvis-muted)]'>
              Paths are repository-relative containers e.g. <code>&quot;.&quot;</code> represent root folder. Duplicate
              values are removed automatically
            </p>
            {!isReadOnly && (
              <button
                type='button'
                onClick={() => {
                  if (formData.paths.some(path => !path.trim())) return;
                  updateField('paths', [...normalizePaths(formData.paths), '']);
                }}
                className='mt-2 inline-flex items-center gap-1.5 px-1 py-1 text-sm font-semibold text-[var(--jarvis-primary)] hover:text-[var(--jarvis-primary-hover)]'
              >
                <PlusIcon className='h-4 w-4' />
                Add path
              </button>
            )}
            {errors.paths && <p className='mt-1 text-sm text-[var(--jarvis-danger-text)]'>{errors.paths}</p>}
          </div>

          {renderInput('GitHub App Client ID', 'githubAppClientId', 'Iv1.xxxxxxxxxxxxxxxx', 'text', true)}
          <InputField
            id='githubAppClientSecret'
            label='GitHub App Client Secret'
            type='password'
            showPasswordToggle={!isReadOnly}
            value={formData.githubAppClientSecret}
            onChange={event => updateField('githubAppClientSecret', event.target.value)}
            disabled={isReadOnly}
            required={!isEditMode || !hasGithubClientSecret}
            placeholder={
              isEditMode && hasGithubClientSecret
                ? 'Leave blank to keep the existing secret'
                : 'GitHub App client secret'
            }
            error={errors.githubAppClientSecret}
            className='mb-6'
            inputClassName='px-4 py-2'
          />
        </>
      )}

      {isAws && (
        <div className='mb-6'>
          <label htmlFor='resourceTagsFilter' className='mb-2 block text-sm font-medium text-[var(--jarvis-text)]'>
            Resource Tags Filter
          </label>
          <input
            id='resourceTagsFilter'
            type='text'
            value={formData.resourceTagsFilter}
            onChange={event => updateField('resourceTagsFilter', event.target.value)}
            disabled={isReadOnly}
            className='w-full rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-4 py-2 text-sm text-[var(--jarvis-text-strong)] shadow-sm focus:border-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:bg-[var(--jarvis-card-muted)] disabled:opacity-50'
            placeholder='e.g., env:production, team:platform'
          />
          <p className='mt-1 text-xs text-[var(--jarvis-muted)]'>
            Optional. Only import resources matching these tags. Comma-separated key:value pairs.
          </p>
        </div>
      )}

      {isEditMode && !isReadOnly && onTestConnection && (
        <div className='mb-6 rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)]/50 p-4'>
          <div className='flex items-center gap-3'>
            <button
              type='button'
              onClick={onTestConnection}
              disabled={testConnectionLoading || testConnectionDisabled}
              className='inline-flex shrink-0 items-center gap-2 rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-4 py-2 text-sm font-medium text-[var(--jarvis-text)] shadow-sm transition-colors hover:bg-[var(--jarvis-card-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-50'
            >
              {testConnectionLoading ? (
                <div className='h-4 w-4 animate-spin rounded-full border-b-2 border-[var(--jarvis-primary)]' />
              ) : (
                <CheckCircleIcon className='h-4 w-4 text-[var(--jarvis-subtle)]' />
              )}
              {testConnectionLoading ? 'Testing...' : 'Test Connection'}
            </button>
            <span
              className={`text-sm ${
                testConnectionResult?.success
                  ? 'text-[var(--jarvis-success-text)]'
                  : testConnectionResult
                    ? 'text-[var(--jarvis-danger-text)]'
                    : 'text-[var(--jarvis-subtle)]'
              }`}
            >
              {testConnectionLoading
                ? 'Connecting...'
                : testConnectionDisabledReason || testConnectionResult?.message || 'Not tested yet'}
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default MainConfigForm;
