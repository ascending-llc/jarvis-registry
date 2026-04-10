import { useEffect, useRef, useState } from 'react';
import { HiBolt, HiCheckCircle } from 'react-icons/hi2';

import FormFields from '@/components/FormFields';
import { useGlobal } from '@/contexts/GlobalContext';
import SERVICES from '@/services';
import type { Agent } from '@/services/agent/type';
import Request from '@/services/request';
import type { AgentConfig } from './types';

const TEST_URL_CANCEL_KEY = 'testAgentUrl';

interface MainConfigFormProps {
  formData: AgentConfig;
  agentDetail: Agent | null;
  updateField: (field: keyof AgentConfig, value: any) => void;
  errors?: Record<string, string | undefined>;
  isReadOnly?: boolean;
}

const MainConfigForm: React.FC<MainConfigFormProps> = ({ formData, agentDetail, updateField, errors, isReadOnly }) => {
  const { showToast } = useGlobal();
  const [discoverStatus, setDiscoverStatus] = useState<'idle' | 'manual' | 'silent'>('idle');
  const [discoveredData, setDiscoveredData] = useState<any>(null);

  const isManualLoading = discoverStatus === 'manual';
  const isSilentLoading = discoverStatus === 'silent';
  const isSameUrl = !!agentDetail?.url && agentDetail.url === formData.url;
  const displayDiscoveredData = discoveredData || (isSameUrl ? agentDetail?.wellKnown : null);

  const handleTestUrl = async (silent = false) => {
    if (!silent && isManualLoading) {
      const cancel = Request.cancels[TEST_URL_CANCEL_KEY];
      if (cancel) {
        cancel();
        delete Request.cancels[TEST_URL_CANCEL_KEY];
      }
      setDiscoverStatus('idle');
      return;
    }

    setDiscoverStatus(silent ? 'silent' : 'manual');
    if (!silent) {
      setDiscoveredData(null);
    }

    try {
      const result = await SERVICES.AGENT.getWellKnownAgentCards(formData.url, {
        cancelTokenKey: TEST_URL_CANCEL_KEY,
      } as any);

      if ((result as any)?.Code === -200) return;

      if (result) {
        setDiscoveredData(result);
        if (!silent) showToast('Agent card discovered successfully', 'success');

        let discoveredType = '';
        if (result.preferredTransport) {
          const transportStr = result.preferredTransport.toLowerCase();
          if (transportStr.includes('grpc')) discoveredType = 'grpc';
          else if (transportStr.includes('jsonrpc')) discoveredType = 'jsonrpc';
          else if (transportStr.includes('http')) discoveredType = 'http_json';
        }

        if (!formData.title && result.name) updateField('title', result.name);
        if (!formData.description && result.description) updateField('description', result.description);
        if (!formData.type && discoveredType) updateField('type', discoveredType);
      } else {
        if (!silent) showToast('Failed to discover agent card', 'error');
      }
    } catch (error: any) {
      if (!silent) {
        const errorMessage =
          typeof error?.detail === 'string'
            ? error.detail
            : typeof error?.detail?.message === 'string'
              ? error.detail.message
              : error?.message || 'Unknown error';
        showToast(errorMessage, 'error');
      }
    } finally {
      setDiscoverStatus(prev => {
        if (silent && prev === 'silent') return 'idle';
        if (!silent && prev === 'manual') return 'idle';
        return prev;
      });
    }
  };

  const hasFetchedRef = useRef(false);
  useEffect(() => {
    if (agentDetail && formData.url && !hasFetchedRef.current) {
      hasFetchedRef.current = true;
      handleTestUrl(true);
    }
  }, [agentDetail, formData.url]);

  const getLineCount = (jsonStr: string) => {
    return jsonStr.split('\n').length;
  };

  return (
    <div className='space-y-8'>
      {/* Section Network Configuration */}
      <section>
        <h3 className='text-lg font-semibold text-gray-900 dark:text-white mb-4'>Network Configuration</h3>
        <div className='space-y-6'>
          {/* Agent URL */}
          <FormFields.InputField
            label='Agent URL'
            type='url'
            name='url'
            id='url'
            required
            disabled={isReadOnly}
            placeholder='https://agent.example.com'
            value={formData.url || ''}
            onChange={e => {
              setDiscoveredData(null);
              updateField('url', e.target.value);
            }}
            helperText='The base URL where your agent is running.'
            suffix={
              <button
                type='button'
                onClick={() => handleTestUrl()}
                disabled={isReadOnly || !formData.url}
                className='btn-input-suffix'
                title={isManualLoading ? 'Cancel test' : isReadOnly ? 'Discover Disabled' : 'Test URL'}
              >
                {isManualLoading ? (
                  <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-gray-600 dark:border-white' />
                ) : displayDiscoveredData ? (
                  <HiCheckCircle className='h-5 w-5 text-green-500' aria-hidden='true' />
                ) : (
                  <HiBolt className='h-5 w-5' aria-hidden='true' />
                )}
              </button>
            }
            error={errors?.url}
          />

          {/* Transport Type */}
          <FormFields.RadioGroupField
            label='Transport Type'
            name='type'
            id='type'
            required
            disabled={isReadOnly}
            options={[
              { label: 'JSONRPC', value: 'jsonrpc' },
              { label: 'GRPC', value: 'grpc' },
              { label: 'HTTP_JSON', value: 'http_json' },
            ]}
            value={formData.type}
            onChange={val => updateField('type', val)}
            error={errors?.type}
          />
        </div>
      </section>

      <section>
        <h3 className='text-lg font-semibold text-gray-900 dark:text-white mb-4'>Basic Information</h3>
        <div className='space-y-6'>
          {/* Title */}
          <FormFields.InputField
            label='Title'
            name='title'
            id='title'
            required
            disabled={isReadOnly}
            placeholder='e.g., My Sales Agent'
            value={formData.title}
            onChange={e => updateField('title', e.target.value)}
            error={errors?.title}
          />

          {/* Description */}
          <FormFields.InputField
            label='Description'
            name='description'
            id='description'
            disabled={isReadOnly}
            placeholder='An agent that handles CRM queries'
            value={formData.description}
            onChange={e => updateField('description', e.target.value)}
            helperText='Describe what this agent does and its capabilities.'
          />

          {/* Path */}
          <FormFields.InputField
            label='Path'
            name='path'
            id='path'
            required
            disabled={isReadOnly}
            placeholder='/my-agent'
            value={formData.path}
            onChange={e => updateField('path', e.target.value)}
            onBlur={e => updateField('path', e.target.value.toLowerCase())}
            helperText='Unique URL path prefix (must start with /)'
            error={errors?.path}
          />
        </div>
      </section>

      {/* Agent Card (Discovered payload) */}
      <section>
        <div className='flex items-center gap-3 mb-1'>
          <h3 className='text-lg font-semibold text-gray-900 dark:text-white m-0'>Agent Card</h3>
          {displayDiscoveredData && !isSilentLoading && (
            <span className='inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'>
              Discovered
            </span>
          )}
          {isSilentLoading && (
            <span className='inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300'>
              Discovering...
            </span>
          )}
          {displayDiscoveredData && !isSilentLoading && (
            <button
              type='button'
              onClick={() => handleTestUrl(true)}
              disabled={isManualLoading}
              className='ml-auto text-xs text-purple-600 hover:text-purple-700 dark:text-purple-400 dark:hover:text-purple-300 cursor-pointer disabled:opacity-50 flex items-center gap-1'
            >
              {isManualLoading ? 'Discovering...' : 'Re-discover'}
            </button>
          )}
        </div>
        {formData.url ? (
          <div className='text-xs text-gray-500 dark:text-gray-400 mb-4'>
            Auto-discovered from{' '}
            <code className='px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700/50 rounded text-purple-600 dark:text-purple-300'>
              {formData.url ? `${formData.url.replace(/\/$/, '')}/.well-known/agent.json` : ''}
            </code>
          </div>
        ) : (
          <div className='text-xs text-gray-500 dark:text-gray-400 mb-4'>No data discovered</div>
        )}

        <div className='border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden'>
          {displayDiscoveredData && (
            <div className='flex items-center justify-between px-3 py-2 bg-gray-50 dark:bg-gray-800/60 border-b border-gray-200 dark:border-gray-700'>
              <span className='text-xs font-mono text-gray-500 dark:text-gray-400'>.well-known/agent.json</span>
              <span className='text-xs font-mono text-gray-500 dark:text-gray-400'>
                {getLineCount(JSON.stringify(displayDiscoveredData, null, 2))} lines
              </span>
            </div>
          )}
          {isSilentLoading || isManualLoading ? (
            <div className='bg-white dark:bg-gray-900/50 p-8 flex items-center justify-center'>
              <div className='animate-spin rounded-full h-8 w-8 border-b-2 border-purple-600' />
            </div>
          ) : displayDiscoveredData ? (
            <pre className='bg-white dark:bg-gray-900/50 text-green-600 dark:text-green-300 font-mono text-xs leading-relaxed whitespace-pre overflow-x-auto p-4 m-0'>
              {JSON.stringify(displayDiscoveredData, null, 2)}
            </pre>
          ) : (
            <div className='bg-white dark:bg-gray-900/50 p-4' />
          )}
        </div>
      </section>

      <section>
        <FormFields.CheckboxField
          label='I trust this agent'
          name='trustAgent'
          id='trustAgent'
          required
          disabled={isReadOnly}
          checked={formData.trustAgent}
          onChange={e => {
            updateField('trustAgent', e.target.checked);
          }}
          description='Custom agents are not verified by Jarvis'
          error={errors?.trustAgent}
        />
      </section>
    </div>
  );
};

export default MainConfigForm;
