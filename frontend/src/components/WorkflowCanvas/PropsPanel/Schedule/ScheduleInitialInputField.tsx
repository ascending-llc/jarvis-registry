import { PlusIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useRef } from 'react';
import type { ScheduleInitialInputMode, ScheduleInputField } from './model';

interface ScheduleInitialInputFieldProps {
  mode: ScheduleInitialInputMode;
  fields: ScheduleInputField[];
  json: string;
  error?: string;
  disabled: boolean;
  onModeChange: (mode: ScheduleInitialInputMode) => void;
  onFieldsChange: (fields: ScheduleInputField[]) => void;
  onJsonChange: (json: string) => void;
}

const INPUT_CLASS =
  'min-w-0 rounded-md border bg-[var(--jarvis-input-bg)] px-2 py-1.5 font-mono text-xs text-[var(--jarvis-text)] shadow-sm outline-none placeholder:text-[var(--jarvis-input-placeholder)] focus:border-[var(--jarvis-primary)] focus:ring-1 focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-50';

const _getDuplicateKeys = (fields: ScheduleInputField[]): Set<string> => {
  const seen = new Set<string>();
  const duplicates = new Set<string>();

  fields.forEach(field => {
    const key = field.key.trim();
    if (!key) return;
    if (seen.has(key)) duplicates.add(key);
    seen.add(key);
  });

  return duplicates;
};

const ScheduleInitialInputField: React.FC<ScheduleInitialInputFieldProps> = ({
  mode,
  fields,
  json,
  error,
  disabled,
  onModeChange,
  onFieldsChange,
  onJsonChange,
}) => {
  const keyInputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const focusAddedField = useRef(false);
  const duplicateKeys = _getDuplicateKeys(fields);

  useEffect(() => {
    if (!focusAddedField.current) return;
    focusAddedField.current = false;
    keyInputRefs.current[fields.length - 1]?.focus();
  }, [fields.length]);

  const updateField = (index: number, field: keyof ScheduleInputField, value: string) => {
    onFieldsChange(fields.map((item, itemIndex) => (itemIndex === index ? { ...item, [field]: value } : item)));
  };

  const addField = () => {
    focusAddedField.current = true;
    onFieldsChange([...fields, { key: '', value: '' }]);
  };

  const removeField = (index: number) => {
    const nextFields = fields.filter((_, itemIndex) => itemIndex !== index);
    onFieldsChange(nextFields.length > 0 ? nextFields : [{ key: '', value: '' }]);
  };

  return (
    <div>
      <div className='mb-1.5 flex items-center justify-between gap-2'>
        <label className='text-xs font-medium text-[var(--jarvis-text)]'>
          Initial input <span className='font-normal text-[var(--jarvis-subtle)]'>optional</span>
        </label>
        <div className='flex rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-0.5'>
          {(['fields', 'json'] as const).map(nextMode => (
            <button
              key={nextMode}
              type='button'
              disabled={disabled}
              onClick={() => onModeChange(nextMode)}
              className={`rounded px-2 py-1 text-[10px] font-medium capitalize transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                mode === nextMode
                  ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                  : 'text-[var(--jarvis-muted)] hover:text-[var(--jarvis-text)]'
              }`}
            >
              {nextMode}
            </button>
          ))}
        </div>
      </div>

      {mode === 'fields' ? (
        <div>
          <div className='space-y-1.5'>
            {fields.map((field, index) => {
              const duplicate = Boolean(field.key.trim() && duplicateKeys.has(field.key.trim()));
              return (
                <div key={index} className='grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_28px] items-center gap-1.5'>
                  <input
                    ref={element => {
                      keyInputRefs.current[index] = element;
                    }}
                    value={field.key}
                    onChange={event => updateField(index, 'key', event.target.value)}
                    disabled={disabled}
                    spellCheck={false}
                    aria-label={`Initial input key ${index + 1}`}
                    placeholder='key'
                    className={`${INPUT_CLASS} ${
                      duplicate ? 'border-[var(--jarvis-danger)]' : 'border-[color:var(--jarvis-input-border)]'
                    }`}
                  />
                  <input
                    value={field.value}
                    onChange={event => updateField(index, 'value', event.target.value)}
                    disabled={disabled}
                    spellCheck={false}
                    aria-label={`Initial input value ${index + 1}`}
                    placeholder='value'
                    className={`${INPUT_CLASS} border-[color:var(--jarvis-input-border)]`}
                  />
                  <button
                    type='button'
                    onClick={() => removeField(index)}
                    disabled={disabled}
                    title='Remove field'
                    aria-label={`Remove initial input field ${index + 1}`}
                    className='inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--jarvis-subtle)] transition-colors hover:bg-[var(--jarvis-danger-soft)] hover:text-[var(--jarvis-danger-text)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-50'
                  >
                    <XMarkIcon className='h-4 w-4' />
                  </button>
                </div>
              );
            })}
          </div>
          <button
            type='button'
            onClick={addField}
            disabled={disabled}
            className='mt-2 inline-flex items-center gap-1 rounded-md px-1 py-1 text-xs font-medium text-[var(--jarvis-primary-text)] transition-colors hover:bg-[var(--jarvis-primary-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-50'
          >
            <PlusIcon className='h-3.5 w-3.5' />
            Add field
          </button>
        </div>
      ) : (
        <textarea
          value={json}
          onChange={event => onJsonChange(event.target.value)}
          disabled={disabled}
          spellCheck={false}
          rows={6}
          className={`w-full resize-y rounded-md border bg-[var(--jarvis-input-bg)] p-2.5 font-mono text-xs leading-relaxed text-[var(--jarvis-text)] outline-none focus:border-[var(--jarvis-primary)] focus:ring-1 focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-50 ${
            error ? 'border-[var(--jarvis-danger)]' : 'border-[color:var(--jarvis-input-border)]'
          }`}
          placeholder={'{\n  "content": "trigger the workflow"\n}'}
        />
      )}

      {error && <p className='mt-1 text-xs text-[var(--jarvis-danger-text)]'>{error}</p>}
      <p className='mt-1 text-[10px] leading-relaxed text-[var(--jarvis-subtle)]'>
        Sent to every scheduled run as its initial input.
      </p>
    </div>
  );
};

export default ScheduleInitialInputField;
