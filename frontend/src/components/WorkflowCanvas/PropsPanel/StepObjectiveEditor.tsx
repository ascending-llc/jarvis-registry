import { Dialog, Transition } from '@headlessui/react';
import { ArrowsPointingOutIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment, useId, useState } from 'react';

interface StepObjectiveEditorProps {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}

const STEP_OBJECTIVE_PLACEHOLDER =
  "What should this step accomplish? e.g. 'Search for the customer's open support tickets from the last 30 days.'";

export const StepObjectiveEditor: React.FC<StepObjectiveEditorProps> = ({ value, disabled, onChange }) => {
  const fieldId = useId();
  const [isOpen, setIsOpen] = useState(false);
  const [draft, setDraft] = useState('');

  const handleOpen = () => {
    setDraft(value);
    setIsOpen(true);
  };

  const handleClose = () => {
    setIsOpen(false);
  };

  const handleSave = () => {
    onChange(draft);
    setIsOpen(false);
  };

  return (
    <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
      <div className='mb-1 flex items-center justify-between gap-2'>
        <label className='block text-xs text-[var(--jarvis-muted)]' htmlFor={fieldId}>
          Step objective *
        </label>
        <div className='flex items-center gap-1.5'>
          <span className='font-mono text-[10px] text-[var(--jarvis-subtle)]'>Markdown</span>
          <button
            type='button'
            onClick={handleOpen}
            aria-label='Open Step objective editor'
            title='Open editor'
            className='inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--jarvis-muted)] transition-colors hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-primary-text)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)]'
          >
            <ArrowsPointingOutIcon className='h-4 w-4' />
          </button>
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
        <Dialog as='div' className='relative z-[120]' onClose={handleClose}>
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
                    <button
                      type='button'
                      onClick={handleClose}
                      aria-label='Close Step objective editor'
                      title='Close'
                      className='inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--jarvis-muted)] transition-colors hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-text)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)]'
                    >
                      <XMarkIcon className='h-4 w-4' />
                    </button>
                  </div>

                  <div className='p-5'>
                    <textarea
                      value={draft}
                      onChange={event => setDraft(event.target.value)}
                      disabled={disabled}
                      autoFocus={!disabled}
                      spellCheck={false}
                      aria-label='Step objective Markdown source'
                      className='h-[360px] min-h-[240px] max-h-[60vh] w-full resize-y overflow-auto rounded-lg border border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-4 py-3 font-mono text-sm leading-relaxed text-[var(--jarvis-text-strong)] outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-60'
                      placeholder={STEP_OBJECTIVE_PLACEHOLDER}
                    />
                  </div>

                  <div className='flex justify-end gap-2 border-t border-[var(--jarvis-border-soft)] px-5 py-4'>
                    {disabled ? (
                      <button
                        type='button'
                        onClick={handleClose}
                        className='rounded-md border border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-3 py-1.5 text-xs font-medium text-[var(--jarvis-text)] transition-colors hover:bg-[var(--jarvis-surface)]'
                      >
                        Close
                      </button>
                    ) : (
                      <>
                        <button
                          type='button'
                          onClick={handleClose}
                          className='rounded-md border border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-3 py-1.5 text-xs font-medium text-[var(--jarvis-text)] transition-colors hover:bg-[var(--jarvis-surface)]'
                        >
                          Cancel
                        </button>
                        <button
                          type='button'
                          onClick={handleSave}
                          className='rounded-md bg-[var(--jarvis-primary)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)]'
                        >
                          Save
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
