import { Dialog, Transition } from '@headlessui/react';
import { ArrowsPointingOutIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment, useId, useRef, useState } from 'react';
import IconButton from '@/components/IconButton';

interface StepObjectiveEditorProps {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onSave: (value: string) => Promise<boolean>;
}

const STEP_OBJECTIVE_PLACEHOLDER =
  "What should this step accomplish? e.g. 'Search for the customer's open support tickets from the last 30 days.'";

export const StepObjectiveEditor: React.FC<StepObjectiveEditorProps> = ({ value, disabled, onChange, onSave }) => {
  const fieldId = useId();
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  const handleOpen = () => {
    setDraft(value);
    setIsOpen(true);
  };

  const handleClose = () => {
    if (isSaving) return;
    setIsOpen(false);
  };

  const handleSave = async () => {
    if (isSaving) return;
    setIsSaving(true);
    try {
      const saved = await onSave(draft);
      if (saved) setIsOpen(false);
    } finally {
      setIsSaving(false);
    }
  };

  const handleDialogKeyDown = (event: React.KeyboardEvent) => {
    if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 's') return;
    event.preventDefault();
    event.stopPropagation();
    if (!disabled && !isSaving) void handleSave();
  };

  return (
    <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
      <div className='mb-1 flex items-center justify-between gap-2'>
        <label className='block text-xs text-[var(--jarvis-muted)]' htmlFor={fieldId}>
          Step objective *
        </label>
        <div className='flex items-center gap-1.5'>
          <span className='font-mono text-[10px] text-[var(--jarvis-subtle)]'>Markdown</span>
          <IconButton
            onClick={handleOpen}
            ariaLabel='Open Step objective editor'
            tooltip='Open editor'
            size='card'
            className='text-[var(--jarvis-muted)] hover:text-[var(--jarvis-primary-text)]'
          >
            <ArrowsPointingOutIcon className='h-4 w-4' />
          </IconButton>
        </div>
      </div>

      <textarea
        id={fieldId}
        value={value}
        onChange={event => onChange(event.target.value)}
        disabled={disabled}
        spellCheck={false}
        className='h-[144px] min-h-[96px] max-h-[320px] w-full resize-y overflow-auto rounded-md border border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-2 py-2 font-mono text-xs leading-relaxed text-[var(--jarvis-text-strong)] outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-60'
        placeholder={STEP_OBJECTIVE_PLACEHOLDER}
      />

      <Transition appear show={isOpen} as={Fragment}>
        <Dialog
          as='div'
          className='relative z-[120]'
          initialFocus={disabled ? undefined : editorRef}
          onClose={handleClose}
          onKeyDown={handleDialogKeyDown}
        >
          <Transition.Child
            as={Fragment}
            enter='ease-out duration-200'
            enterFrom='opacity-0'
            enterTo='opacity-100'
            leave='ease-in duration-150'
            leaveFrom='opacity-100'
            leaveTo='opacity-0'
          >
            <div className='fixed inset-0 bg-black/50 backdrop-blur-sm' />
          </Transition.Child>

          <div className='fixed inset-0 overflow-y-auto'>
            <div className='flex min-h-full items-center justify-center p-4'>
              <Transition.Child
                as={Fragment}
                enter='ease-out duration-200'
                enterFrom='opacity-0 scale-95'
                enterTo='opacity-100 scale-100'
                leave='ease-in duration-150'
                leaveFrom='opacity-100 scale-100'
                leaveTo='opacity-0 scale-95'
              >
                <Dialog.Panel className='w-full max-w-3xl transform overflow-hidden rounded-xl border border-[var(--jarvis-border)] bg-[var(--jarvis-card)] shadow-2xl transition-all'>
                  <div className='flex items-center justify-between border-b border-[var(--jarvis-border-soft)] px-5 py-4'>
                    <div className='flex items-center gap-2.5'>
                      <div className='flex h-7 w-7 items-center justify-center rounded-md bg-[var(--jarvis-primary)] text-[10px] font-bold tracking-wider text-white shadow-sm'>
                        MD
                      </div>
                      <div>
                        <Dialog.Title
                          as='h3'
                          className='text-[15px] font-medium text-[var(--jarvis-text-strong)]'
                        >
                          Step objective
                        </Dialog.Title>
                        <p className='mt-0.5 text-[11px] text-[var(--jarvis-subtle)]'>Markdown source</p>
                      </div>
                    </div>
                    <IconButton
                      onClick={handleClose}
                      disabled={isSaving}
                      ariaLabel='Close Step objective editor'
                      tooltip='Close'
                      size='card'
                      className='text-[var(--jarvis-muted)] hover:text-[var(--jarvis-text)]'
                    >
                      <XMarkIcon className='h-4 w-4' />
                    </IconButton>
                  </div>

                  <div className='p-5'>
                    <textarea
                      ref={editorRef}
                      value={draft}
                      onChange={event => setDraft(event.target.value)}
                      disabled={disabled || isSaving}
                      spellCheck={false}
                      aria-label='Step objective Markdown source'
                      className='h-[360px] min-h-[240px] max-h-[60vh] w-full resize-y overflow-auto rounded-lg border border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-4 py-3 font-mono text-sm leading-relaxed text-[var(--jarvis-text-strong)] outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-60'
                      placeholder={STEP_OBJECTIVE_PLACEHOLDER}
                    />
                  </div>

                  <div className='flex items-center justify-end gap-2 border-t border-[var(--jarvis-border-soft)] bg-[var(--jarvis-card)] px-5 py-3.5'>
                    {disabled ? (
                      <button
                        type='button'
                        onClick={handleClose}
                        className='rounded-md border border-[var(--jarvis-border)] bg-transparent px-4 py-1.5 text-[13px] text-[var(--jarvis-subtle)] transition-colors hover:border-[var(--jarvis-border-strong)] hover:text-[var(--jarvis-text)]'
                      >
                        Close
                      </button>
                    ) : (
                      <>
                        <button
                          type='button'
                          onClick={handleClose}
                          disabled={isSaving}
                          className='rounded-md border border-[var(--jarvis-border)] bg-transparent px-4 py-1.5 text-[13px] text-[var(--jarvis-subtle)] transition-colors hover:border-[var(--jarvis-border-strong)] hover:text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-50'
                        >
                          Cancel
                        </button>
                        <button
                          type='button'
                          onClick={() => void handleSave()}
                          disabled={isSaving}
                          className='flex items-center gap-1.5 rounded-md border border-transparent bg-[var(--jarvis-primary)] px-4 py-1.5 text-[13px] font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50'
                        >
                          {isSaving && (
                            <span className='h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-b-white' />
                          )}
                          {isSaving ? 'Saving...' : 'Save'}
                        </button>
                      </>
                    )}
                  </div>
                </Dialog.Panel>
              </Transition.Child>
            </div>
          </div>
        </Dialog>
      </Transition>
    </div>
  );
};

export default StepObjectiveEditor;
