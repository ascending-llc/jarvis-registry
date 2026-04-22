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
    'block w-full rounded-md border shadow-sm sm:text-sm text-[var(--jarvis-text)] placeholder:text-[var(--jarvis-input-placeholder)] focus:border-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)]';

  const borderClass = error
    ? 'border-[var(--jarvis-danger)] focus:border-[var(--jarvis-danger)] focus:ring-[var(--jarvis-danger)]'
    : 'border-[color:var(--jarvis-input-border)]';

  const disabledClass = disabled ? 'disabled:opacity-50 disabled:cursor-not-allowed' : '';

  const paddingClass = isPasswordToggle ? 'pr-10' : '';

  return (
    <div className={className}>
      {label && (
        <label htmlFor={generatedId} className={`flex items-center justify-between mb-1 ${labelClassName}`}>
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
      <div className={suffix ? 'flex gap-2' : ''}>
        <div className='relative flex-1'>
          <input
            id={generatedId}
            type={resolvedType}
            disabled={disabled}
            className={`${baseInputClass} ${borderClass} ${disabledClass} ${paddingClass} ${inputClassName}`}
            style={{
              ...(monospace ? { fontFamily: 'Menlo, Consolas, Courier New, monospace' } : {}),
              backgroundColor: 'var(--jarvis-input-bg)',
            }}
            required={required}
            {...props}
          />
          {isPasswordToggle && (
            <button
              type='button'
              onClick={() => setPasswordVisible(!passwordVisible)}
              className='absolute inset-y-0 right-0 flex items-center pr-3 text-[var(--jarvis-icon)] hover:text-[var(--jarvis-icon-hover)] focus:outline-none'
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
      {helperText && <div className='mt-1 text-xs text-[var(--jarvis-muted)]'>{helperText}</div>}
      {error && <p className='mt-1 text-xs text-[var(--jarvis-danger-text)]'>{error}</p>}
    </div>
  );
};
