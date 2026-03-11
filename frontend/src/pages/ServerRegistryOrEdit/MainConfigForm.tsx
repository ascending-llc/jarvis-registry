import { useState } from 'react';
import { HiBolt, HiCheckCircle } from 'react-icons/hi2';
import FormFields from '@/components/FormFields';
import { useGlobal } from '@/contexts/GlobalContext';
import SERVICES from '@/services';
import Request from '@/services/request';
import type { GetServersDetailResponse } from '@/services/server/type';
import AuthenticationConfig from './AuthenticationConfig';
import type { ServerConfig } from './types';

const TEST_URL_CANCEL_KEY = 'testServerUrl';

interface MainConfigFormProps {
  formData: ServerConfig;
  serverDetail: GetServersDetailResponse | null;
  isEditMode?: boolean;
  updateField: (field: keyof ServerConfig, value: any) => void;
  errors?: Record<string, string | undefined>;
  isReadOnly?: boolean;
}

const MainConfigForm: React.FC<MainConfigFormProps> = ({
  formData,
  serverDetail,
  isEditMode,
  updateField,
  errors,
  isReadOnly,
}) => {
  const { showToast } = useGlobal();
  const [testingUrl, setTestingUrl] = useState(false);
  const [urlTestPassed, setUrlTestPassed] = useState(false);

  const handleUpdateField = (field: keyof ServerConfig, value: any) => {
    updateField(field, value);
  };

  const handleTestUrl = async () => {
    // 如果正在请求中，取消请求
    if (testingUrl) {
      const cancel = Request.cancels[TEST_URL_CANCEL_KEY];
      if (cancel) {
        cancel();
        delete Request.cancels[TEST_URL_CANCEL_KEY];
      }
      setTestingUrl(false);
      setUrlTestPassed(false);
      return;
    }

    setTestingUrl(true);
    setUrlTestPassed(false);
    try {
      const result = await SERVICES.SERVER.testServerUrl({ url: formData.url, transport: formData.type }, {
        cancelTokenKey: TEST_URL_CANCEL_KEY,
      } as any);
      // 请求被取消时不提示
      if ((result as any)?.Code === -200) return;
      if (result?.success) {
        setUrlTestPassed(true);
        showToast(result.message, 'success');
      } else {
        showToast(result.message, 'error');
      }
    } catch (error: any) {
      const errorMessage =
        typeof error?.detail === 'string'
          ? error.detail
          : typeof error?.detail?.message === 'string'
            ? error.detail.message
            : error?.message || 'Unknown error';
      showToast(errorMessage, 'error');
    } finally {
      setTestingUrl(false);
    }
  };

  const handleAuthChange = (newConfig: any) => {
    updateField('authConfig', newConfig);
  };

  return (
    <div className='space-y-8'>
      {/* Section Basic Information */}
      <section>
        <h3 className='text-lg font-semibold text-gray-900 dark:text-white mb-2'>Basic Information</h3>
        <div className='space-y-6'>
          {/* Title */}
          <FormFields.InputField
            label='Title'
            name='title'
            id='title'
            required
            disabled={isReadOnly}
            placeholder='e.g., My Knowledge Base'
            value={formData.title}
            onChange={e => handleUpdateField('title', e.target.value)}
            error={errors?.title}
          />

          {/* Description */}
          <FormFields.InputField
            label='Description (optional)'
            name='description'
            id='description'
            disabled={isReadOnly}
            placeholder='what does this server do and what tools it exposes'
            value={formData.description}
            onChange={e => handleUpdateField('description', e.target.value)}
            helperText="Be specific about capabilities (e.g., 'Exposes tools for querying PostgreSQL databases')"
          />

          {/* Path */}
          <FormFields.InputField
            label='Path'
            name='path'
            id='path'
            required
            disabled={isReadOnly}
            placeholder='/my-server'
            value={formData.path}
            onChange={e => handleUpdateField('path', e.target.value)}
            onBlur={e => handleUpdateField('path', e.target.value.toLowerCase())}
            helperText='Unique URL path prefix (must start with /)'
            error={errors?.path}
          />
        </div>
      </section>

      {/* Section Network Configuration */}
      <section>
        <h3 className='text-lg font-semibold text-gray-900 dark:text-white mb-2'>Network Configuration</h3>
        <div className='space-y-6'>
          {/* MCP Server URL */}
          <FormFields.InputField
            label='MCP Server URL'
            type='url'
            name='url'
            id='url'
            required
            disabled={isReadOnly}
            placeholder='https://server.example.com:81'
            value={formData.url || ''}
            onChange={e => {
              setUrlTestPassed(false);
              handleUpdateField('url', e.target.value);
            }}
            helperText='Internal URL where your MCP server is running'
            suffix={
              <button
                type='button'
                onClick={handleTestUrl}
                disabled={isReadOnly || !formData.url}
                className='btn-input-suffix'
                title={testingUrl ? 'Cancel test' : 'Test URL'}
              >
                {testingUrl ? (
                  <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-gray-600 dark:border-white ' />
                ) : urlTestPassed ? (
                  <HiCheckCircle className='h-5 w-5 text-green-500' aria-hidden='true' />
                ) : (
                  <HiBolt className='h-5 w-5' aria-hidden='true' />
                )}
              </button>
            }
            error={errors?.url}
          />

          {/* Server Type */}
          <FormFields.RadioGroupField
            label='Server Type'
            value={formData.type}
            onChange={val => handleUpdateField('type', val)}
            options={[
              { label: 'Streamable HTTPS', value: 'streamable-http' },
              { label: 'SSE', value: 'sse' },
            ]}
            disabled={isReadOnly}
          />
        </div>
      </section>

      {/* Custom Settings */}
      <section>
        <h3 className='text-lg font-semibold text-gray-900 dark:text-white'>Custom Settings</h3>
        <span className='block text-xs text-gray-500 dark:text-gray-400 mb-4'>
          Configure advanced options for this MCP server.
        </span>
        <FormFields.KeyValueListField
          value={
            formData.headers === null
              ? []
              : Object.entries(formData.headers).map(([key, val]) => ({ key, value: String(val) }))
          }
          onChange={val => {
            const newHeaders = Object.fromEntries(val.map(v => [v.key, v.value]));
            handleUpdateField('headers', newHeaders);
          }}
          disabled={isReadOnly}
          maxItems={5}
          className='mt-2'
          validateEmpty={!!errors?.headers}
        />
      </section>

      {/* Section Authentication */}
      <AuthenticationConfig
        config={formData.authConfig}
        onChange={handleAuthChange}
        errors={errors}
        path={formData.path}
        mcpUrl={formData.url}
        isEditMode={isEditMode}
        isReadOnly={isReadOnly}
        hasOauth={!!serverDetail?.oauth}
      />

      {/* Section Metadata */}
      <section>
        <h3 className='text-lg font-semibold text-gray-900 dark:text-white mb-2'>Metadata</h3>
        <div className='space-y-6'>
          {/* Tags */}
          <FormFields.InputField
            label='Tags (optional)'
            name='tags'
            id='tags'
            disabled={isReadOnly}
            placeholder='tag1,tag2,tag3'
            value={formData.tags?.join(',') || ''}
            onChange={e => {
              const val = e.target.value;
              const tagsArray = val ? val.split(',') : [];
              handleUpdateField('tags', tagsArray);
            }}
          />

          {/* Trust Checkbox */}
          <FormFields.CheckboxField
            label='I trust this application'
            name='trustServer'
            id='trustServer'
            required
            disabled={isReadOnly}
            checked={formData.trustServer}
            onChange={e => {
              handleUpdateField('trustServer', e.target.checked);
            }}
            description='Custom connectors are not verified by Jarvis'
            error={errors?.trustServer}
          />
        </div>
      </section>
    </div>
  );
};

export default MainConfigForm;
