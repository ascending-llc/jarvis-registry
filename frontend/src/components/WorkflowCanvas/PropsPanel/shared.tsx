import type React from 'react';
import { useEffect, useState } from 'react';

export interface AddButtonProps {
  children: React.ReactNode;
  onClick: () => void;
}

export const AddButton: React.FC<AddButtonProps> = ({ children, onClick }) => {
  return (
    <button
      className='w-full bg-none border border-dashed border-[var(--jarvis-border-strong)] rounded-md text-[var(--jarvis-subtle)] font-sans text-xs py-1.5 cursor-pointer transition-colors duration-150 hover:border-[var(--jarvis-primary)] hover:text-[var(--jarvis-primary-text)]'
      onClick={onClick}
    >
      {children}
    </button>
  );
};

export interface BranchListProps {
  items: string[];
  onAdd: () => void;
  onRm: (i: number) => void;
  onChange?: (i: number, val: string) => void;
  addLabel: string;
  prefix?: string;
}

export const BranchList: React.FC<BranchListProps> = ({ items, onAdd, onRm, onChange, addLabel, prefix }) => {
  return (
    <>
      <div className='branch-list'>
        {items.map((item, i) => (
          <div
            key={item || `empty-${i}`}
            className='flex items-center gap-1.5 bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md px-2 py-1.5 mb-1'
          >
            {prefix && <span className='font-mono text-[10px] text-[var(--jarvis-subtle)] shrink-0'>{prefix}</span>}
            <LocalStateInput
              className='font-mono text-[11px] text-[var(--jarvis-text)] flex-1 bg-transparent border-none outline-none'
              value={item}
              onChange={val => onChange?.(i, val)}
            />
            <button
              className='shrink-0 rounded p-0.5 transition-colors hover:bg-[var(--jarvis-danger-soft)] hover:text-[var(--jarvis-danger-text)] bg-none border-none text-[var(--jarvis-subtle)] cursor-pointer text-[13px]'
              onClick={() => onRm(i)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
      <AddButton onClick={onAdd}>{addLabel}</AddButton>
    </>
  );
};

export interface LocalStateInputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'onChange'> {
  value: string;
  onChange: (val: string) => void;
}

export const LocalStateInput: React.FC<LocalStateInputProps> = ({ value, onChange, ...props }) => {
  const [localValue, setLocalValue] = useState(value);

  // Sync with upstream value if it changes externally
  useEffect(() => {
    setLocalValue(value);
  }, [value]);

  return (
    <input
      {...props}
      value={localValue}
      onChange={e => setLocalValue(e.target.value)}
      onBlur={() => onChange(localValue)}
      onKeyDown={e => {
        if (e.key === 'Enter') {
          e.currentTarget.blur(); // Trigger onBlur and thus onChange
        }
      }}
    />
  );
};
