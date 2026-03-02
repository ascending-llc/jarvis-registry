import type React from 'react';

/**
 * Common Props for all form fields
 */
export interface BaseFieldProps {
  label?: React.ReactNode;
  labelTag?: string;
  className?: string;
  error?: string;
  helperText?: React.ReactNode;
  required?: boolean;
  disabled?: boolean;
  id?: string;
}

/**
 * RadioGroupField Props
 */
export interface RadioOption {
  label: string;
  value: string | number;
}
