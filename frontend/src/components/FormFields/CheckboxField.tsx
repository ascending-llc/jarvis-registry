import type React from 'react';
import type { BaseFieldProps } from './types';

/**
 * CheckboxField Props
 */
export interface CheckboxFieldProps extends BaseFieldProps, Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  description?: React.ReactNode;
}

/**
 * Reusable Checkbox Field Component
 */
export const CheckboxField: React.FC<CheckboxFieldProps> = ({
  label,
  description,
  className = '',
  error,
  required,
  disabled,
  id,
  ...props
}) => {
  const generatedId = id || props.name;

  return (
    <div className={`flex items-start ${className}`}>
      <div className='flex h-5 items-center'>
        <input
          id={generatedId}
          type='checkbox'
          disabled={disabled}
          required={required}
          className={`h-4 w-4 rounded border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] text-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-50 ${
            error ? 'ring-2 ring-[var(--jarvis-danger)]' : ''
          }`}
          {...props}
        />
      </div>
      <div className='ml-3 text-sm'>
        {label && (
          <label htmlFor={generatedId} className='font-medium text-[var(--jarvis-text)]'>
            {label} {required && <span className='text-[var(--jarvis-danger)]'>*</span>}
          </label>
        )}
        {description && <div className='text-[var(--jarvis-muted)]'>{description}</div>}
        {error && <p className='mt-1 text-xs text-[var(--jarvis-danger-text)]'>{error}</p>}
      </div>
    </div>
  );
};
