import { CpuChipIcon, InformationCircleIcon, TrashIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useState } from 'react';

import { InputField, SelectField } from '@/components/FormFields';
import type { ModelGatewaySelection, ModelSourceListItem } from '@/services/model/type';

import { AWS_REGION_OPTIONS, isBackendSupportedProvider, MODEL_PROVIDER_OPTIONS, PROVIDER_LABELS } from './constants';
import ModelBackLink from './ModelBackLink';
import ModelDefaultBadge from './ModelDefaultBadge';
import { slugifyModelName } from './modelForm';
import type { ModelFormErrors, ModelFormProviderType, ModelFormState } from './types';

type FieldUpdater = <Key extends keyof ModelFormState>(field: Key, value: ModelFormState[Key]) => void;

type ProviderFieldsProps = {
  form: ModelFormState;
  errors: ModelFormErrors;
  hasStoredApiKey: boolean;
  updateField: FieldUpdater;
};

const AwsFields: React.FC<ProviderFieldsProps> = ({ form, errors, updateField }) => (
  <div className='space-y-4'>
    <div className='grid gap-4 sm:grid-cols-2'>
      <SelectField
        id='aws-region'
        label='AWS region'
        required
        value={form.awsRegion}
        options={AWS_REGION_OPTIONS.map(region => ({ value: region, label: region }))}
        onChange={value => updateField('awsRegion', value)}
        error={errors.awsRegion}
      />
      <InputField
        name='baseModelId'
        label='Base model ID'
        required
        monospace
        value={form.baseModelId}
        onChange={event => updateField('baseModelId', event.target.value)}
        placeholder='anthropic.claude-sonnet-4-5-20250929-v1:0'
        helperText='Canonical foundation model ID used for context-window and pricing metadata.'
        error={errors.baseModelId}
      />
    </div>
    <InputField
      name='modelIdOrArn'
      label='Model ID or inference profile ARN'
      required
      monospace
      value={form.modelIdOrArn}
      onChange={event => updateField('modelIdOrArn', event.target.value)}
      placeholder='arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/abc123'
      error={errors.modelIdOrArn}
    />
    <div className='flex gap-2.5 rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-3.5 text-sm leading-5 text-[var(--jarvis-muted)]'>
      <InformationCircleIcon className='mt-0.5 h-4 w-4 shrink-0 text-[var(--jarvis-primary-text)]' />
      <p>
        No credentials are stored. Jarvis calls Bedrock with its own IAM role, which needs{' '}
        <code className='text-xs text-[var(--jarvis-text)]'>bedrock:InvokeModel</code> permission for this model.
      </p>
    </div>
  </div>
);

const AzureFields: React.FC<ProviderFieldsProps> = ({ form, errors, hasStoredApiKey, updateField }) => (
  <div className='space-y-4'>
    <InputField
      name='endpoint'
      label='Endpoint'
      required
      monospace
      value={form.endpoint}
      onChange={event => updateField('endpoint', event.target.value)}
      placeholder='https://your-resource.openai.azure.com'
      error={errors.endpoint}
    />
    <div className='grid gap-4 sm:grid-cols-3'>
      <InputField
        name='deploymentName'
        label='Deployment name'
        required
        monospace
        value={form.deploymentName}
        onChange={event => updateField('deploymentName', event.target.value)}
        placeholder='gpt4o-prod'
        error={errors.deploymentName}
      />
      <InputField
        name='baseModelId'
        label='Base model ID'
        required
        monospace
        value={form.baseModelId}
        onChange={event => updateField('baseModelId', event.target.value)}
        placeholder='gpt-4o'
        error={errors.baseModelId}
      />
      <InputField
        name='apiVersion'
        label='API version'
        required
        monospace
        value={form.apiVersion}
        onChange={event => updateField('apiVersion', event.target.value)}
        placeholder='2024-10-21'
        error={errors.apiVersion}
      />
    </div>
    <InputField
      name='apiKey'
      type='password'
      showPasswordToggle
      monospace
      label='API key'
      labelTag='Optional'
      value={form.apiKey}
      onChange={event => updateField('apiKey', event.target.value)}
      placeholder={hasStoredApiKey ? 'Key on file. Leave blank to keep it.' : 'Paste a fallback API key'}
      helperText={
        form.mode === 'embedding'
          ? 'Embedding selection requires an API key; Workload Identity is not supported on that path.'
          : 'Jarvis uses Workload Identity first. A supplied key is encrypted and used as fallback.'
      }
    />
  </div>
);

const DirectProviderFields: React.FC<ProviderFieldsProps> = ({ form, errors, hasStoredApiKey, updateField }) => {
  const isOpenAI = form.providerType === 'openai';
  return (
    <div className='space-y-4'>
      <div className='rounded-lg border border-[var(--jarvis-warning)]/40 bg-[var(--jarvis-warning-soft)] p-3.5 text-sm leading-5 text-[var(--jarvis-warning-text)]'>
        This provider form is ready for review. Saving will be enabled when backend ModelSource support is available.
      </div>
      <div className='grid gap-4 sm:grid-cols-2'>
        <InputField
          name='baseModelId'
          label='Model ID'
          required
          monospace
          value={form.baseModelId}
          onChange={event => updateField('baseModelId', event.target.value)}
          placeholder={isOpenAI ? (form.mode === 'chat' ? 'gpt-4.1' : 'text-embedding-3-large') : 'claude-sonnet-4-5'}
          error={errors.baseModelId}
        />
        <InputField
          name='baseUrl'
          label='Base URL'
          labelTag='Optional'
          monospace
          value={form.baseUrl}
          onChange={event => updateField('baseUrl', event.target.value)}
          placeholder={isOpenAI ? 'https://api.openai.com/v1' : 'https://api.anthropic.com'}
          helperText='Leave blank for the public API; set this for a proxy or regional endpoint.'
          error={errors.baseUrl}
        />
      </div>
      <InputField
        name='apiKey'
        type='password'
        showPasswordToggle
        monospace
        label='API key'
        required={!hasStoredApiKey}
        labelTag={hasStoredApiKey ? 'Optional' : undefined}
        value={form.apiKey}
        onChange={event => updateField('apiKey', event.target.value)}
        placeholder={hasStoredApiKey ? 'Key on file. Leave blank to keep it.' : isOpenAI ? 'sk-...' : 'sk-ant-...'}
        helperText={
          hasStoredApiKey
            ? 'Leave blank to preserve the stored key.'
            : 'This field will be encrypted at rest when backend support is added.'
        }
        error={errors.apiKey}
      />
    </div>
  );
};

const ProviderFields: React.FC<ProviderFieldsProps> = props => {
  if (props.form.providerType === 'aws_bedrock') return <AwsFields {...props} />;
  if (props.form.providerType === 'azure_openai') return <AzureFields {...props} />;
  return <DirectProviderFields {...props} />;
};

const validateForm = (form: ModelFormState, hasStoredApiKey: boolean): ModelFormErrors => {
  const errors: ModelFormErrors = {};
  if (!form.displayName.trim()) errors.displayName = 'Display name is required';
  else if (form.displayName.trim().length > 128) errors.displayName = 'Display name must be 128 characters or fewer';
  if (!form.baseModelId.trim()) errors.baseModelId = 'Model ID is required';

  if (form.providerType === 'aws_bedrock') {
    if (!form.awsRegion.trim()) errors.awsRegion = 'AWS region is required';
    if (!form.modelIdOrArn.trim()) errors.modelIdOrArn = 'Model ID or ARN is required';
  } else if (form.providerType === 'azure_openai') {
    if (!form.endpoint.trim()) errors.endpoint = 'Endpoint is required';
    else if (!/^https:\/\//i.test(form.endpoint)) errors.endpoint = 'Endpoint must start with https://';
    if (!form.deploymentName.trim()) errors.deploymentName = 'Deployment name is required';
    if (!form.apiVersion.trim()) errors.apiVersion = 'API version is required';
  } else {
    if (!hasStoredApiKey && !form.apiKey.trim()) errors.apiKey = 'API key is required';
    if (form.baseUrl.trim() && !/^https:\/\//i.test(form.baseUrl)) errors.baseUrl = 'Base URL must start with https://';
  }
  return errors;
};

type ModelFormViewProps = {
  initialForm: ModelFormState;
  editing: boolean;
  loading?: boolean;
  loadError?: string | null;
  saving: boolean;
  deleting?: boolean;
  canWrite: boolean;
  hasStoredApiKey: boolean;
  defaultModel?: Pick<ModelSourceListItem, 'id' | 'mode'> | null;
  selection: ModelGatewaySelection;
  onCancel: () => void;
  onDelete?: () => void;
  onRetry?: () => void;
  onSubmit: (form: ModelFormState) => void;
};

const ModelFormView: React.FC<ModelFormViewProps> = ({
  initialForm,
  editing,
  loading = false,
  loadError = null,
  saving,
  deleting = false,
  canWrite,
  hasStoredApiKey,
  defaultModel = null,
  selection,
  onCancel,
  onDelete,
  onRetry,
  onSubmit,
}) => {
  const [form, setForm] = useState<ModelFormState>(initialForm);
  const [errors, setErrors] = useState<ModelFormErrors>({});

  const updateField: FieldUpdater = (field, value) => {
    setForm(current => ({ ...current, [field]: value }));
    setErrors(current => (current[field] ? { ...current, [field]: undefined } : current));
  };

  const handleSubmit = () => {
    const nextErrors = validateForm(form, hasStoredApiKey);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length === 0) onSubmit(form);
  };

  const providerOptions = MODEL_PROVIDER_OPTIONS.map(provider => ({
    value: provider.value,
    label: `${provider.label} — ${provider.auth}`,
  }));
  const providerSupported = isBackendSupportedProvider(form.providerType);
  const pageTitle = editing
    ? initialForm.displayName
      ? `Edit ${initialForm.displayName}`
      : 'Edit model'
    : 'Add model';

  return (
    <div className='custom-scrollbar -mr-4 h-full overflow-y-auto sm:-mr-6 lg:-mr-8'>
      {editing && (
        <div className='mx-auto w-3/4'>
          <ModelBackLink label='Models' onBack={onCancel} disabled={saving || deleting} />
        </div>
      )}
      <div className='mx-auto flex min-h-full w-3/4 flex-col rounded-lg bg-[var(--jarvis-card)]'>
        <div className='flex items-center gap-4 border-b border-[color:var(--jarvis-border)] px-6 py-6'>
          <div className='flex items-center justify-center rounded-xl bg-[var(--jarvis-primary-soft)] p-3 text-[var(--jarvis-primary-text)]'>
            <CpuChipIcon className='h-8 w-8' />
          </div>
          <div>
            <div className='flex flex-wrap items-center gap-2'>
              <h1 className='text-2xl font-bold text-[var(--jarvis-text-strong)]'>{pageTitle}</h1>
              {defaultModel && <ModelDefaultBadge model={defaultModel} selection={selection} />}
            </div>
            <p className='mt-0.5 text-base text-[var(--jarvis-muted)]'>Configure a cloud-hosted model source</p>
          </div>
        </div>

        {loading ? (
          <div className='flex min-h-[240px] flex-1 items-center justify-center'>
            <div className='h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]' />
          </div>
        ) : loadError ? (
          <div className='flex min-h-[240px] flex-1 flex-col items-center justify-center px-6 text-center'>
            <p className='text-base font-semibold text-[var(--jarvis-text-strong)]'>Failed to load model</p>
            <p className='mt-2 max-w-lg text-sm text-[var(--jarvis-muted)]'>{loadError}</p>
            <div className='mt-5 flex gap-3'>
              <button
                type='button'
                onClick={onCancel}
                className='rounded-md border border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] px-4 py-2 text-sm font-medium text-[var(--jarvis-text)]'
              >
                Back
              </button>
              {onRetry && (
                <button type='button' onClick={onRetry} className='btn-primary'>
                  Retry
                </button>
              )}
            </div>
          </div>
        ) : (
          <>
            <div className='flex flex-1 flex-col px-6 py-4'>
              <div className='space-y-8'>
                {!canWrite && (
                  <div className='rounded-lg border border-[var(--jarvis-danger)]/30 bg-[var(--jarvis-danger-soft)] p-3 text-sm text-[var(--jarvis-danger-text)]'>
                    Your account does not have the models-write scope. You can review this form but cannot save changes.
                  </div>
                )}

                <section>
                  <h3 className='mb-2 text-lg font-semibold text-[var(--jarvis-text-strong)]'>Basic Information</h3>
                  <div className='space-y-6'>
                    <InputField
                      name='displayName'
                      label='Display name'
                      required
                      maxLength={128}
                      value={form.displayName}
                      onChange={event => updateField('displayName', event.target.value)}
                      placeholder='Claude Sonnet 4.5'
                      helperText={
                        <>
                          Callers use{' '}
                          <code className='font-mono text-[var(--jarvis-primary-text)]'>
                            {slugifyModelName(form.displayName)}
                          </code>{' '}
                          as the model name
                        </>
                      }
                      error={errors.displayName}
                    />

                    <div>
                      <label
                        htmlFor='model-description'
                        className='mb-1 block text-sm font-medium text-[var(--jarvis-text)]'
                      >
                        Description (optional)
                      </label>
                      <textarea
                        id='model-description'
                        rows={2}
                        value={form.description}
                        onChange={event => updateField('description', event.target.value)}
                        placeholder='What teams should use this model for'
                        className='block w-full resize-y rounded-md border border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] px-3 py-2 text-sm text-[var(--jarvis-text)] shadow-sm outline-none placeholder:text-[var(--jarvis-input-placeholder)] focus:border-[var(--jarvis-primary)] focus:ring-1 focus:ring-[var(--jarvis-primary)]'
                      />
                    </div>

                    <div className='grid gap-4 sm:grid-cols-2'>
                      <SelectField
                        id='model-provider'
                        label='Provider'
                        required
                        value={form.providerType}
                        options={providerOptions}
                        onChange={value => updateField('providerType', value as ModelFormProviderType)}
                      />
                      <div>
                        <div className='mb-1 text-sm font-medium text-[var(--jarvis-text)]'>
                          Mode <span className='text-[var(--jarvis-danger)]'>*</span>
                        </div>
                        <div className='flex rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-bg)] p-1'>
                          {(['chat', 'embedding'] as const).map(mode => (
                            <button
                              key={mode}
                              type='button'
                              onClick={() => updateField('mode', mode)}
                              className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                                form.mode === mode
                                  ? 'bg-[var(--jarvis-card)] text-[var(--jarvis-text)] shadow-sm'
                                  : 'text-[var(--jarvis-muted)] hover:text-[var(--jarvis-text)]'
                              }`}
                            >
                              {mode === 'chat' ? 'Chat' : 'Embedding'}
                            </button>
                          ))}
                        </div>
                        <p className='mt-1 text-xs text-[var(--jarvis-muted)]'>
                          {form.mode === 'chat' ? 'Can back workflows and chat' : 'Powers semantic search'}
                        </p>
                      </div>
                    </div>
                  </div>
                </section>

                <section>
                  <div className='mb-4 flex items-center justify-between gap-3'>
                    <h3 className='text-lg font-semibold text-[var(--jarvis-text-strong)]'>
                      {PROVIDER_LABELS[form.providerType]} Connection
                    </h3>
                    <span className='rounded bg-[var(--jarvis-primary-soft)] px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-[var(--jarvis-primary-text)]'>
                      {form.providerType === 'aws_bedrock'
                        ? 'IAM role'
                        : form.providerType === 'azure_openai'
                          ? 'Workload Identity'
                          : 'API key'}
                    </span>
                  </div>
                  <ProviderFields
                    form={form}
                    errors={errors}
                    hasStoredApiKey={hasStoredApiKey}
                    updateField={updateField}
                  />
                  {!providerSupported && (
                    <p className='mt-3 text-xs text-[var(--jarvis-muted)]'>
                      OpenAI and Anthropic ModelSource APIs are pending backend implementation.
                    </p>
                  )}
                </section>
              </div>
            </div>

            <div className='flex flex-wrap items-center justify-between gap-4 border-t border-[color:var(--jarvis-border)] px-6 py-4'>
              <div>
                {editing && canWrite && onDelete && (
                  <button
                    type='button'
                    aria-label='Delete model'
                    title='Delete model'
                    onClick={onDelete}
                    disabled={saving || deleting}
                    className='inline-flex items-center rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-4 py-2 text-sm font-medium text-[var(--jarvis-danger-text)] shadow-sm hover:bg-[var(--jarvis-danger-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-danger)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50'
                  >
                    <TrashIcon className='h-4 w-4' />
                  </button>
                )}
              </div>
              <div className='flex gap-3'>
                <button
                  type='button'
                  onClick={onCancel}
                  disabled={saving || deleting}
                  className='min-w-[80px] rounded-md border border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] px-4 py-2 text-sm font-medium text-[var(--jarvis-text)] shadow-sm focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 sm:min-w-[120px] md:min-w-[160px]'
                >
                  Cancel
                </button>
                <button
                  type='button'
                  onClick={handleSubmit}
                  disabled={saving || deleting || !canWrite || !providerSupported}
                  title={providerSupported ? undefined : 'Backend ModelSource support is not available yet'}
                  className='inline-flex min-w-[80px] items-center justify-center gap-2 rounded-md border border-transparent bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-[var(--jarvis-primary-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 sm:min-w-[120px] md:min-w-[160px]'
                >
                  {saving && <div className='h-4 w-4 animate-spin rounded-full border-b-2 border-white' />}
                  {editing ? 'Update' : 'Create'}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default ModelFormView;
