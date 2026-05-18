import { Fragment, useRef, useState } from 'react';
import { Listbox, Transition } from '@headlessui/react';
import { CheckIcon, ChevronDownIcon } from '@heroicons/react/24/outline';
import { createPortal } from 'react-dom';
import type React from 'react';
import type { BaseFieldProps } from './types';

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectFieldProps extends BaseFieldProps {
  options: SelectOption[];
  value?: string;
  onChange?: (value: string) => void;
  defaultValue?: string;
  placeholder?: string;
}

/**
 * Reusable Select Field Component
 * Uses Headless UI Listbox for full custom styling consistent with the design system.
 * Supports controlled (value + onChange) and uncontrolled (defaultValue) usage.
 */
export const SelectField: React.FC<SelectFieldProps> = ({
  label,
  labelTag,
  options,
  value: controlledValue,
  onChange,
  defaultValue,
  placeholder = 'Select an option',
  className = '',
  error,
  helperText,
  required,
  disabled,
  id,
}) => {
  const isControlled = controlledValue !== undefined;
  const [internalValue, setInternalValue] = useState<string>(defaultValue ?? options[0]?.value ?? '');
  const selectedValue = isControlled ? controlledValue : internalValue;
  const selectedOption = options.find(o => o.value === selectedValue);
  const btnRef = useRef<HTMLButtonElement>(null);

  const handleChange = (val: string) => {
    if (!isControlled) setInternalValue(val);
    onChange?.(val);
  };

  const borderClass = error
    ? 'border-[var(--jarvis-danger)]'
    : 'border-[color:var(--jarvis-input-border)]';

  const focusClass = error
    ? 'focus:border-[var(--jarvis-danger)] focus:ring-[var(--jarvis-danger)]'
    : 'focus:border-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)]';

  return (
    <div className={className}>
      {label && (
        <label htmlFor={id} className='flex items-center justify-between mb-1'>
          <span className='text-sm font-medium text-[var(--jarvis-text)]'>
            {label} {required && <span className='text-[var(--jarvis-danger)]'>*</span>}
          </span>
          {labelTag && (
            <span className='rounded bg-[var(--jarvis-primary-soft)] px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-[var(--jarvis-primary-text)]'>
              {labelTag}
            </span>
          )}
        </label>
      )}

      <Listbox value={selectedValue} onChange={handleChange} disabled={disabled}>
        {({ open }) => {
          const rect = btnRef.current?.getBoundingClientRect();
          const dropdownTop = rect ? rect.bottom + 4 : 0;
          const dropdownLeft = rect ? rect.left : 0;
          const dropdownWidth = rect ? rect.width : 200;

          return (
            <div className='relative'>
              <Listbox.Button
                ref={btnRef}
                className={`block w-full rounded-md border shadow-sm sm:text-sm text-left relative outline-none transition-colors
                  disabled:cursor-not-allowed disabled:opacity-50
                  ${borderClass} ${focusClass}
                  ${open ? 'border-[var(--jarvis-primary)] ring-1 ring-[var(--jarvis-primary)]' : ''}
                `}
                style={{
                  backgroundColor: 'var(--jarvis-input-bg)',
                  color: selectedOption ? 'var(--jarvis-text)' : 'var(--jarvis-input-placeholder)',
                  padding: '6px 36px 6px 12px',
                }}
              >
                <span className='block truncate'>
                  {selectedOption ? selectedOption.label : placeholder}
                </span>
                <span className='pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2.5'>
                  <ChevronDownIcon
                    className={`h-4 w-4 text-[var(--jarvis-icon)] transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
                    aria-hidden='true'
                  />
                </span>
              </Listbox.Button>

              {typeof document !== 'undefined' &&
                createPortal(
                  <Transition
                    show={open}
                    as={Fragment}
                    leave='transition ease-in duration-100'
                    leaveFrom='opacity-100'
                    leaveTo='opacity-0'
                  >
                    <Listbox.Options
                      className='fixed z-[300] overflow-auto rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] p-1.5 text-sm shadow-xl focus:outline-none'
                      style={{
                        top: dropdownTop,
                        left: dropdownLeft,
                        width: dropdownWidth,
                        maxHeight: 220,
                      }}
                    >
                      {options.map(option => (
                        <Listbox.Option key={option.value} value={option.value} as={Fragment}>
                          {({ active, selected }) => (
                            <li
                              className={`relative cursor-pointer select-none rounded-md py-2 pl-9 pr-3 transition-colors ${
                                active
                                  ? 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text-strong)]'
                                  : 'text-[var(--jarvis-text)]'
                              }`}
                            >
                              {selected && (
                                <span className='absolute inset-y-0 left-0 flex items-center pl-3 text-[var(--jarvis-primary-text)]'>
                                  <CheckIcon className='h-4 w-4' aria-hidden='true' />
                                </span>
                              )}
                              <span className={selected ? 'font-medium' : 'font-normal'}>{option.label}</span>
                            </li>
                          )}
                        </Listbox.Option>
                      ))}
                    </Listbox.Options>
                  </Transition>,
                  document.body,
                )}
            </div>
          );
        }}
      </Listbox>

      {helperText && <div className='mt-1 text-xs text-[var(--jarvis-muted)]'>{helperText}</div>}
      {error && <p className='mt-1 text-xs text-[var(--jarvis-danger-text)]'>{error}</p>}
    </div>
  );
};
