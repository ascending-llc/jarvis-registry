import { EyeIcon, EyeSlashIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useState } from 'react';
import type { BaseFieldProps } from './types';

/**
 * InputField Props
 */
export interface InputFieldProps extends BaseFieldProps, Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  labelClassName?: string;
  inputClassName?: string;
  /** 当 type='password' 时，是否显示密码可见性切换按钮 */
  showPasswordToggle?: boolean;
  /** 是否使用等宽字体 */
  monospace?: boolean;
  /** 输入框右侧的附加元素（如操作按钮） */
  suffix?: React.ReactNode;
}

/**
 * Reusable Input Field Component
 * Supports text, password, url, email etc.
 */
export const InputField: React.FC<InputFieldProps> = ({
  label,
  labelTag,
  className = '',
  error,
  helperText,
  required,
  disabled,
  id,
  type,
  labelClassName = '',
  inputClassName = '',
  showPasswordToggle = false,
  monospace = false,
  suffix,
  ...props
}) => {
  const [passwordVisible, setPasswordVisible] = useState(false);
  const generatedId = id || props.name;

  const isPasswordToggle = showPasswordToggle && type === 'password';
  const resolvedType = isPasswordToggle ? (passwordVisible ? 'text' : 'password') : type;

  const baseInputClass =
    'block w-full rounded-md shadow-sm focus:border-purple-500 focus:ring-purple-500 sm:text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500';

  const borderClass = error ? 'border-red-500 focus:border-red-500' : 'border-gray-300 dark:border-gray-600';

  const disabledClass = disabled ? 'disabled:opacity-50 disabled:cursor-not-allowed' : '';

  const paddingClass = isPasswordToggle ? 'pr-10' : '';

  return (
    <div className={className}>
      {label && (
        <label htmlFor={generatedId} className={`flex items-center justify-between mb-1 ${labelClassName}`}>
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
      <div className={suffix ? 'flex gap-2' : ''}>
        <div className='relative flex-1'>
          <input
            id={generatedId}
            type={resolvedType}
            disabled={disabled}
            className={`${baseInputClass} ${borderClass} ${disabledClass} ${paddingClass} ${inputClassName}`}
            style={monospace ? { fontFamily: 'Menlo, Consolas, Courier New, monospace' } : undefined}
            required={required}
            {...props}
          />
          {isPasswordToggle && (
            <button
              type='button'
              onClick={() => setPasswordVisible(!passwordVisible)}
              className='absolute inset-y-0 right-0 flex items-center pr-3 text-gray-400 hover:text-gray-500 dark:text-gray-500 dark:hover:text-gray-400 focus:outline-none'
            >
              {passwordVisible ? (
                <EyeSlashIcon className='h-5 w-5' aria-hidden='true' />
              ) : (
                <EyeIcon className='h-5 w-5' aria-hidden='true' />
              )}
            </button>
          )}
        </div>
        {suffix}
      </div>
      {helperText && <div className='mt-1 text-xs text-gray-500 dark:text-gray-400'>{helperText}</div>}
      {error && <p className='mt-1 text-xs text-red-500'>{error}</p>}
    </div>
  );
};
