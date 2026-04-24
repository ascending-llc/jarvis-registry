import { CheckCircleIcon, ExclamationCircleIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

type ToastType = 'success' | 'error';

interface GlobalContextType {
  showToast: (message: string, type: ToastType) => void;
}

const GlobalContext = createContext<GlobalContextType | undefined>(undefined);

export const useGlobal = () => {
  const context = useContext(GlobalContext);
  if (context === undefined) {
    throw new Error('useGlobal must be used within a GlobalProvider');
  }
  return context;
};

// Toast Component (Internal)
interface ToastProps {
  message: string;
  type: ToastType;
  onClose: () => void;
}

const Toast: React.FC<ToastProps> = ({ message, type, onClose }) => {
  useEffect(() => {
    const timer = setTimeout(() => {
      onClose();
    }, 4000);
    return () => clearTimeout(timer);
  }, [onClose, message]);

  return createPortal(
    <div
      className="fixed top-4 right-4 z-[100] max-w-full animate-slide-in-top"
      onClick={e => e.stopPropagation()}
      onMouseDown={e => e.stopPropagation()}
    >
      <div
        className={`flex items-center p-4 rounded-lg shadow-lg border ${
 type === 'success'
 ? 'bg-[var(--jarvis-success-soft)] border-[color:var(--jarvis-success-soft)] text-[var(--jarvis-success-text)] bg-[var(--jarvis-success-soft)]/50 border-[color:var(--jarvis-success-soft)] text-[var(--jarvis-success-text)]'
 : 'bg-[var(--jarvis-danger-soft)] border-[color:var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)] bg-[var(--jarvis-danger-soft)]/50 border-[color:var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)]'
 }`}
      >
        {type === 'success' ? (
          <CheckCircleIcon className="h-5 w-5 mr-3 flex-shrink-0" />
        ) : (
          <ExclamationCircleIcon className="h-5 w-5 mr-3 flex-shrink-0" />
        )}
        <p className="text-sm font-medium max-w-full truncate">{message}</p>
        <button
          onClick={e => {
            e.stopPropagation();
            onClose();
          }}
          onMouseDown={e => e.stopPropagation()}
          className="ml-3 flex-shrink-0 text-current opacity-70 hover:opacity-100"
        >
          <XMarkIcon className="h-4 w-4" />
        </button>
      </div>
    </div>,
    document.body,
  );
};

export const GlobalProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [toast, setToast] = useState<{ message: string; type: ToastType } | null>(null);

  const showToast = useCallback((message: string, type: ToastType) => {
    setToast({ message, type });
  }, []);

  const hideToast = useCallback(() => {
    setToast(null);
  }, []);

  return (
    <GlobalContext.Provider value={{ showToast }}>
      {children}
      {toast && <Toast message={toast.message} type={toast.type} onClose={hideToast} />}
    </GlobalContext.Provider>
  );
};
