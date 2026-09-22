export const getErrorMessage = (error: unknown, fallback: string): string => {
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object') {
    const err = error as {
      detail?: string | { message?: string; error?: string };
      message?: string;
      error?: string;
    };
    if (typeof err.detail === 'string') return err.detail;
    if (err.detail && typeof err.detail === 'object') {
      if (typeof err.detail.message === 'string') return err.detail.message;
      if (typeof err.detail.error === 'string') return err.detail.error;
    }
    if (typeof err.message === 'string') return err.message;
    if (typeof err.error === 'string') return err.error;
  }
  return fallback;
};
