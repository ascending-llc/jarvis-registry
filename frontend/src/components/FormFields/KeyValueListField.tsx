import { Disclosure, Transition } from '@headlessui/react';
import {
  ChevronDownIcon,
  ChevronUpIcon,
  DocumentDuplicateIcon,
  PlusIcon,
  TrashIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { InputField } from './InputField';

export interface KeyValuePair {
  key: string;
  value: string;
}

export interface KeyValueListFieldProps {
  value?: KeyValuePair[];
  onChange?: (value: KeyValuePair[]) => void;
  keyLabel?: string;
  valueLabel?: string;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
  addLabel?: string;
  disabled?: boolean;
  className?: string;
  maxItems?: number;
  validateEmpty?: boolean;
}

export const KeyValueListField: React.FC<KeyValueListFieldProps> = ({
  value = [],
  onChange,
  keyLabel = 'HEADER NAME',
  valueLabel = 'HEADER VALUE',
  keyPlaceholder = 'e.g. Authorization',
  valuePlaceholder = 'e.g. Bearer token...',
  addLabel = 'Add Header',
  disabled = false,
  className = '',
  maxItems,
  validateEmpty = false,
}) => {
  const handleAdd = () => {
    if (disabled || !onChange || (maxItems !== undefined && value.length >= maxItems)) return;
    onChange([...value, { key: '', value: '' }]);
  };

  const handleUpdate = (index: number, field: 'key' | 'value', newValue: string) => {
    if (disabled || !onChange) return;
    const newItems = [...value];
    newItems[index] = { ...newItems[index], [field]: newValue };
    onChange(newItems);
  };

  const handleDelete = (index: number) => {
    if (disabled || !onChange) return;
    const newItems = value.filter((_, i) => i !== index);
    onChange(newItems);
  };

  const handleCopy = (index: number) => {
    const itemToCopy = value[index];
    const json = JSON.stringify({ [itemToCopy.key]: itemToCopy.value });
    navigator.clipboard.writeText(json);
  };

  const duplicateKeys = new Set<string>();
  const seenKeys = new Set<string>();
  value.forEach(item => {
    if (item.key && seenKeys.has(item.key)) {
      duplicateKeys.add(item.key);
    }
    seenKeys.add(item.key);
  });

  return (
    <div className={`space-y-4 ${className}`}>
      {value.map((item, index) => {
        const hasDuplicateError = item.key && duplicateKeys.has(item.key);
        const hasEmptyKeyError = validateEmpty && !item.key.trim();
        const hasEmptyValueError = validateEmpty && !item.value.trim();

        return (
          <Disclosure key={index} defaultOpen={true}>
            {({ open }) => {
              const hasAnyError = hasDuplicateError || hasEmptyKeyError || hasEmptyValueError;
              const borderClass = hasAnyError
                ? 'border-[var(--jarvis-danger)]/50'
                : 'border-[color:var(--jarvis-border)]';

              const ringClass = open
                ? hasAnyError
                  ? 'ring-1 ring-[var(--jarvis-danger)]/30'
                  : 'ring-1 ring-[var(--jarvis-primary)]/20'
                : '';

              return (
                <div className={`rounded-md border bg-[var(--jarvis-card)] shadow-sm ${borderClass} ${ringClass}`}>
                  <div className='flex w-full items-center justify-between rounded-t-md bg-[var(--jarvis-card-muted)] px-4 py-3'>
                    <span
                      className={`truncate text-sm font-medium ${hasAnyError ? 'text-[var(--jarvis-danger-text)]' : 'text-[var(--jarvis-text)]'}`}
                    >
                      {item.key || 'New Header'}
                    </span>

                    <div className='flex items-center gap-2'>
                      <button
                        type='button'
                        onClick={e => {
                          e.stopPropagation();
                          handleCopy(index);
                        }}
                        className='flex items-center gap-1.5 rounded bg-[var(--jarvis-success-soft)] px-2 py-1 text-xs font-medium text-[var(--jarvis-success-text)] transition-colors hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50'
                      >
                        <DocumentDuplicateIcon className='w-3.5 h-3.5' />
                        Copy
                      </button>
                      <button
                        type='button'
                        onClick={e => {
                          e.stopPropagation();
                          handleDelete(index);
                        }}
                        disabled={disabled}
                        className='flex items-center gap-1.5 rounded bg-[var(--jarvis-danger-soft)] px-2 py-1 text-xs font-medium text-[var(--jarvis-danger-text)] transition-colors hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50'
                      >
                        <TrashIcon className='w-3.5 h-3.5' />
                        Delete
                      </button>
                      <Disclosure.Button className='rounded p-1 text-[var(--jarvis-primary-text)] transition-colors hover:bg-[var(--jarvis-primary-soft)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--jarvis-primary)]'>
                        {open ? <ChevronUpIcon className='w-5 h-5' /> : <ChevronDownIcon className='w-5 h-5' />}
                      </Disclosure.Button>
                    </div>
                  </div>

                  <Transition
                    enter='transition duration-100 ease-out'
                    enterFrom='transform scale-95 opacity-0'
                    enterTo='transform scale-100 opacity-100'
                    leave='transition duration-75 ease-out'
                    leaveFrom='transform scale-100 opacity-100'
                    leaveTo='transform scale-95 opacity-0'
                  >
                    <Disclosure.Panel className='space-y-4 border-t border-[color:var(--jarvis-border)] px-4 pb-4 pt-4'>
                      <InputField
                        label={keyLabel}
                        labelClassName='!text-xs !font-bold !text-[var(--jarvis-muted)] !tracking-wider uppercase'
                        value={item.key}
                        onChange={e => handleUpdate(index, 'key', e.target.value)}
                        placeholder={keyPlaceholder}
                        error={
                          hasDuplicateError
                            ? 'Header name must be unique'
                            : hasEmptyKeyError
                              ? 'Header name cannot be empty'
                              : undefined
                        }
                        disabled={disabled}
                        monospace
                      />
                      <InputField
                        label={valueLabel}
                        labelClassName='!text-xs !font-bold !text-[var(--jarvis-muted)] !tracking-wider uppercase'
                        value={item.value}
                        onChange={e => handleUpdate(index, 'value', e.target.value)}
                        placeholder={valuePlaceholder}
                        error={hasEmptyValueError ? 'Header value cannot be empty' : undefined}
                        disabled={disabled}
                        monospace
                      />
                    </Disclosure.Panel>
                  </Transition>
                </div>
              );
            }}
          </Disclosure>
        );
      })}

      {(!maxItems || value.length < maxItems) && (
        <button type='button' onClick={handleAdd} disabled={disabled} className='btn-input-suffix'>
          <PlusIcon className='w-4 h-4' />
          {addLabel}
        </button>
      )}
      {maxItems !== undefined && value.length >= maxItems && (
        <div className='mt-2 px-1 text-xs text-[var(--jarvis-muted)]'>Maximum of {maxItems} headers reached.</div>
      )}
    </div>
  );
};
