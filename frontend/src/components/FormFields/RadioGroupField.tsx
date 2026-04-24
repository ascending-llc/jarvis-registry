import type React from 'react';
import type { BaseFieldProps, RadioOption } from './types';

export interface RadioGroupFieldProps extends BaseFieldProps {
  options: RadioOption[];
  value: string | number;
  onChange: (value: any) => void;
  name?: string;
}

/**
 * Reusable Radio Group Field Component (Segmented Control style)
 */
export const RadioGroupField: React.FC<RadioGroupFieldProps> = ({
  label,
  labelTag,
  options,
  value,
  onChange,
  className = '',
  disabled,
  error,
  helperText,
  required,
  id,
}) => {
  const borderClass = error ? 'border border-[var(--jarvis-danger)]' : '';

  return (
    <div className={className}>
      {label && (
        <label className='flex items-center justify-between mb-1'>
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
      <div className={`flex rounded-lg bg-[var(--jarvis-card-muted)] p-1 ${borderClass}`}>
        {options.map(option => (
          <button
            key={option.value}
            id={id ? `${id}-${option.value}` : undefined}
            type='button'
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={`flex-1 py-2 text-sm font-medium rounded-md transition-all duration-200 ${
              value === option.value
                ? 'bg-[var(--jarvis-card)] text-[var(--jarvis-text-strong)] shadow-sm'
                : 'text-[var(--jarvis-muted)] hover:text-[var(--jarvis-text)]'
            } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
          >
            {option.label}
          </button>
        ))}
      </div>
      {helperText && <div className='mt-1 text-xs text-[var(--jarvis-muted)]'>{helperText}</div>}
      {error && <p className='mt-1 text-xs text-[var(--jarvis-danger-text)]'>{error}</p>}
    </div>
  );
};
