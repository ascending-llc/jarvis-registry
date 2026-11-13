import { Listbox } from '@headlessui/react';
import { CheckIcon, ChevronDownIcon, ExclamationTriangleIcon } from '@heroicons/react/24/outline';
import { type ReactNode, useId } from 'react';
import { Link } from 'react-router-dom';
import { useServer } from '@/contexts/ServerContext';
import { PROVIDER_LABELS } from '@/pages/Models/constants';
import { APP_ROUTES } from '@/routes';

interface ModelSourceSectionProps {
  modelSourceId: string | null;
  isReadOnly: boolean;
  onChange: (modelSourceId: string | null) => void;
}

const DEFAULT_HELP = 'LLM that drives this step. Leave on workspace default unless the step needs a specific model.';

const ModelOption = ({ value, title, detail }: { value: string | null; title: string; detail: string }) => (
  <Listbox.Option
    value={value}
    className={({ active }) =>
      `flex cursor-pointer items-start gap-2 rounded px-2 py-1.5 ${active ? 'bg-[var(--jarvis-card-muted)]' : ''}`
    }
  >
    {({ selected }) => (
      <>
        <span className='w-3.5 shrink-0 text-[var(--jarvis-primary-text)]'>
          {selected && <CheckIcon aria-hidden='true' className='h-3.5 w-3.5' />}
        </span>
        <span className='min-w-0'>
          <span className='block truncate text-xs text-[var(--jarvis-text-strong)]' title={title}>
            {title}
          </span>
          <span className='block text-[10px] text-[var(--jarvis-subtle)]'>{detail}</span>
        </span>
      </>
    )}
  </Listbox.Option>
);

export const ModelSourceSection = ({ modelSourceId, isReadOnly, onChange }: ModelSourceSectionProps) => {
  const { models, modelLoading, modelError, modelSelection, modelSelectionError, refreshModelData } = useServer();
  const helpId = useId();
  const chatModels = models.filter(model => model.mode === 'chat');
  const selected = chatModels.find(model => model.id === modelSourceId);
  const defaultId = modelSelection.defaultWorkflowModelSourceId;
  const defaultName =
    modelSelectionError == null ? models.find(model => model.id === defaultId)?.displayName : undefined;
  const defaultUnset = modelSelectionError == null && defaultId == null;
  const deleted = !modelLoading && modelError == null && modelSourceId != null && !selected;

  let defaultDetail = 'Follows the workspace default.';
  let fallback = 'the workspace default';
  if (defaultName) {
    defaultDetail = `Currently ${defaultName}. Follows changes to the default.`;
    fallback = `the workspace default (${defaultName})`;
  } else if (defaultUnset) {
    defaultDetail = "Not configured. Runs use the server's built-in fallback model.";
    fallback = "the server's built-in fallback model (no workspace default is set)";
  }

  let trigger: ReactNode;
  let help: ReactNode = DEFAULT_HELP;
  if (modelLoading) {
    trigger = <span className='truncate text-[var(--jarvis-subtle)]'>Loading models…</span>;
  } else if (modelError != null) {
    trigger =
      modelSourceId == null ? (
        'Workspace default'
      ) : (
        <>
          <span className='shrink-0'>Unavailable ·</span>
          <span className='truncate font-mono text-[11px]' title={modelSourceId}>
            {modelSourceId}
          </span>
        </>
      );
    help = (
      <span className='text-[var(--jarvis-danger-text)]'>
        Couldn't load models.{modelSourceId != null && ' The current selection is kept.'}{' '}
        <button type='button' className='underline' onClick={() => void refreshModelData()}>
          Retry
        </button>
      </span>
    );
  } else if (deleted) {
    trigger = (
      <>
        <ExclamationTriangleIcon aria-hidden='true' className='h-3.5 w-3.5 shrink-0 text-amber-500' />
        <span className='truncate text-[var(--jarvis-text-strong)]'>
          Unknown model <span className='text-[var(--jarvis-subtle)]'>(deleted)</span>
        </span>
      </>
    );
    help = (
      <span className='text-[var(--jarvis-warning-text)]'>
        The selected model source no longer exists. Runs fall back to {fallback}, and saving is blocked until you pick
        another model.
      </span>
    );
  } else if (selected) {
    trigger = (
      <>
        <span className='truncate text-[var(--jarvis-text-strong)]' title={selected.displayName}>
          {selected.displayName}
        </span>
        <span className='shrink-0 rounded border border-[var(--jarvis-border)] bg-[var(--jarvis-card)] px-1.5 font-mono text-[10px] text-[var(--jarvis-muted)]'>
          {PROVIDER_LABELS[selected.providerType]}
        </span>
      </>
    );
  } else {
    trigger = (
      <span className='truncate'>
        Workspace default{defaultName ? ` · ${defaultName}` : defaultUnset ? ' · not configured' : ''}
      </span>
    );
    if (defaultUnset) {
      help = (
        <>
          No workspace default is set, so runs use the server's built-in fallback model. An admin can set a default on
          the{' '}
          <Link to={APP_ROUTES.models} className='text-[var(--jarvis-primary-text)] underline'>
            Models page
          </Link>
          .
        </>
      );
    }
  }

  return (
    <div className='border-b border-[var(--jarvis-border)] px-4 py-3'>
      <Listbox value={modelSourceId} onChange={onChange} disabled={isReadOnly || modelLoading || modelError != null}>
        <div className='relative'>
          <Listbox.Label className='mb-1 block text-xs text-[var(--jarvis-muted)]'>Model</Listbox.Label>
          <Listbox.Button
            aria-describedby={helpId}
            className={`flex w-full items-center gap-1.5 rounded-md border bg-[var(--jarvis-card-muted)] px-2 py-1.5 text-left text-xs text-[var(--jarvis-subtle)] outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-60 ${deleted ? 'border-amber-600' : 'border-[var(--jarvis-border)]'}`}
          >
            {trigger}
            <ChevronDownIcon aria-hidden='true' className='ml-auto h-3.5 w-3.5 shrink-0' />
          </Listbox.Button>
          <Listbox.Options className='absolute z-50 mt-1 max-h-60 w-full overflow-y-auto rounded-md border border-[var(--jarvis-border)] bg-[var(--jarvis-card)] p-1 shadow-lg focus:outline-none'>
            <ModelOption value={null} title='Workspace default' detail={defaultDetail} />
            {chatModels.length > 0 && (
              <li aria-hidden='true' role='presentation' className='my-1 border-t border-[var(--jarvis-border)]' />
            )}
            {chatModels.map(model => (
              <ModelOption
                key={model.id}
                value={model.id}
                title={model.displayName}
                detail={[PROVIDER_LABELS[model.providerType], model.description].filter(Boolean).join(' · ')}
              />
            ))}
          </Listbox.Options>
        </div>
      </Listbox>
      <p id={helpId} className='mt-1.5 text-[10px] leading-relaxed text-[var(--jarvis-subtle)]'>
        {help}
      </p>
    </div>
  );
};
