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
  const borderClass = error ? 'border border-red-500' : '';

  return (
    <div className={className}>
      {label && (
        <label className='flex items-center justify-between mb-1'>
          <span className='text-sm font-medium text-gray-900 dark:text-gray-100'>
            {label} {required && <span className='text-red-500'>*</span>}
          </span>
          {labelTag && (
            <span className='text-xs font-semibold uppercase tracking-wide px-2 py-0.5 rounded bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400'>
              {labelTag}
            </span>
          )}
        </label>
      )}
      <div className={`flex p-1 bg-gray-200 dark:bg-gray-700/50 rounded-lg ${borderClass}`}>
        {options.map(option => (
          <button
            key={option.value}
            id={id ? `${id}-${option.value}` : undefined}
            type='button'
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={`flex-1 py-2 text-sm font-medium rounded-md transition-all duration-200 ${
              value === option.value
                ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
                : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
            } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
          >
            {option.label}
          </button>
        ))}
      </div>
      {helperText && <div className='mt-1 text-xs text-gray-500 dark:text-gray-400'>{helperText}</div>}
      {error && <p className='mt-1 text-xs text-red-500'>{error}</p>}
    </div>
  );
};
