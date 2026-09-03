import { ArrowLeftIcon, EyeIcon, PencilSquareIcon } from '@heroicons/react/24/outline';
import type React from 'react';

import { SKILL_MARKDOWN_PATH } from './constants';
import SkillCategoryMenu from './SkillCategoryMenu';
import SkillContentPanel from './SkillContentPanel';
import SkillFileTree from './SkillFileTree';
import { formatSkillVersion, getSkillMarkdownInput, splitSkillMarkdown } from './skillDraft';
import type { EditorMode, SkillDraft, SkillPageError } from './types';

type SkillEditorViewProps = {
  draft: SkillDraft | null;
  loading: boolean;
  error: SkillPageError | null;
  selectedPath: string;
  editorMode: EditorMode;
  saving: boolean;
  toggling: boolean;
  onBack: () => void;
  onRetry: () => void;
  onSelectFile: (path: string) => void;
  onEditorModeChange: (mode: EditorMode) => void;
  onNameChange: (name: string) => void;
  onDescriptionChange: (description: string) => void;
  onMarkdownChange: (markdown: string) => void;
  onCategoryChange: (category: string) => void;
  alwaysApply: boolean;
  onAlwaysApplyChange: () => void;
  onToggle: () => void;
  onReset: () => void;
  onSave: () => void;
};

const SkillEditorView: React.FC<SkillEditorViewProps> = ({
  draft,
  loading,
  error,
  selectedPath,
  editorMode,
  saving,
  toggling,
  onBack,
  onRetry,
  onSelectFile,
  onEditorModeChange,
  onNameChange,
  onDescriptionChange,
  onMarkdownChange,
  onCategoryChange,
  alwaysApply,
  onAlwaysApplyChange,
  onToggle,
  onReset,
  onSave,
}) => {
  if (loading) {
    return (
      <section className='flex min-h-0 flex-1 flex-col px-0 pt-2 md:px-8'>
        <button
          type='button'
          onClick={onBack}
          className='mb-6 inline-flex w-fit items-center gap-2 text-[13.5px] text-[var(--jarvis-muted)]'
        >
          <ArrowLeftIcon className='h-4 w-4' /> Skills
        </button>
        <div className='flex flex-1 flex-col items-center justify-center gap-3 text-sm text-[var(--jarvis-muted)]'>
          <div className='h-8 w-8 animate-spin rounded-full border-2 border-[color:var(--jarvis-border-strong)] border-b-[var(--jarvis-spinner)]' />
          Loading skill...
        </div>
      </section>
    );
  }

  if (error || !draft) {
    const title =
      error?.kind === 'forbidden'
        ? 'You do not have access to this skill'
        : error?.kind === 'not-found'
          ? 'Skill not found'
          : 'Failed to load skill';
    return (
      <section className='flex min-h-0 flex-1 flex-col px-0 pt-2 md:px-8'>
        <button
          type='button'
          onClick={onBack}
          className='mb-6 inline-flex w-fit items-center gap-2 text-[13.5px] text-[var(--jarvis-muted)]'
        >
          <ArrowLeftIcon className='h-4 w-4' /> Skills
        </button>
        <div className='flex flex-1 flex-col items-center justify-center px-6 text-center'>
          <p className='text-lg font-semibold text-[var(--jarvis-text-strong)]'>{title}</p>
          <p className='mt-2 max-w-lg text-sm text-[var(--jarvis-muted)]'>{error?.message}</p>
          {error?.kind === 'generic' && (
            <button type='button' onClick={onRetry} className='btn-primary mt-5'>
              Retry
            </button>
          )}
        </div>
      </section>
    );
  }

  const canEdit = draft.id === null || draft.permissions?.EDIT === true;
  const isSkillMarkdown = selectedPath === SKILL_MARKDOWN_PATH;
  const parsedMarkdown = draft.markdown.parsed;
  const markdownError = draft.markdown.invalidInput?.message ?? null;
  let frontmatterSource = '';
  try {
    frontmatterSource = splitSkillMarkdown(getSkillMarkdownInput(draft.markdown)).frontmatterSource;
  } catch {
    // The preview renders the raw invalid Markdown below, so no frontmatter source is needed.
  }
  const supportingFiles = draft.files.filter(file => file.relativePath !== SKILL_MARKDOWN_PATH);
  const selectedMetadata = draft.files.find(file => file.relativePath === selectedPath);

  return (
    <section className='flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden px-0 pt-2 md:px-8'>
      <div className='mb-5 flex flex-shrink-0 items-center gap-2'>
        <button
          type='button'
          aria-label='Back to Skills'
          onClick={onBack}
          className='inline-flex h-7 w-7 items-center justify-center rounded-[7px] text-[var(--jarvis-muted)] transition hover:bg-[var(--jarvis-card-muted)] hover:text-[var(--jarvis-text)]'
        >
          <ArrowLeftIcon className='h-[15px] w-[15px]' />
        </button>
        <button
          type='button'
          onClick={onBack}
          className='text-[13.5px] text-[var(--jarvis-muted)] transition hover:text-[var(--jarvis-text)]'
        >
          Skills
        </button>
      </div>

      <div className='flex flex-shrink-0 flex-wrap items-start justify-between gap-6'>
        <div className='min-w-0 flex-1 basis-80'>
          <div className='mb-2 text-[12.5px] text-[var(--jarvis-muted)]'>Name</div>
          {editorMode === 'edit' && canEdit ? (
            <input
              type='text'
              value={parsedMarkdown.displayTitle}
              maxLength={128}
              aria-label='Skill name'
              placeholder='Skill name'
              disabled={markdownError !== null}
              onChange={event => onNameChange(event.target.value)}
              className='w-full max-w-3xl border-0 border-b border-transparent bg-transparent px-0 py-0.5 text-[32px] font-bold leading-[1.15] tracking-[-0.01em] text-[var(--jarvis-text-strong)] outline-none transition-colors placeholder:text-[var(--jarvis-faint)] hover:border-[color:var(--jarvis-border-strong)] focus:border-[var(--jarvis-primary)] focus:ring-0 disabled:cursor-not-allowed disabled:opacity-60'
            />
          ) : parsedMarkdown.displayTitle ? (
            <h1 className='break-words text-[32px] font-bold leading-[1.15] tracking-[-0.01em] text-[var(--jarvis-text-strong)]'>
              {parsedMarkdown.displayTitle}
            </h1>
          ) : (
            <h1 className='text-[32px] font-bold leading-[1.15] text-[var(--jarvis-faint)]'>Untitled skill</h1>
          )}
          <div className='mt-2.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[13px] text-[var(--jarvis-muted)]'>
            <span>{formatSkillVersion(draft.version)}</span>
            <span aria-hidden='true'>·</span>
            <span>{draft.authorName || 'You'}</span>
          </div>
        </div>

        <div className='relative flex flex-wrap items-center justify-end gap-2.5'>
          {editorMode === 'preview' && canEdit && (
            <button
              type='button'
              role='switch'
              aria-checked={draft.enabled}
              disabled={toggling || saving}
              onClick={onToggle}
              className='inline-flex items-center gap-2 text-[13px] text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-60'
            >
              <span>{toggling ? 'Updating...' : draft.enabled ? 'Enabled' : 'Disabled'}</span>
              <span
                className={`relative h-[22px] w-[38px] flex-shrink-0 rounded-full transition ${
                  draft.enabled ? 'bg-[var(--jarvis-primary)]' : 'bg-[var(--jarvis-border-strong)]'
                }`}
              >
                <span
                  className={`absolute left-0 top-0.5 h-[18px] w-[18px] rounded-full bg-white shadow transition-transform ${
                    draft.enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
                  }`}
                />
              </span>
            </button>
          )}
          {canEdit && isSkillMarkdown && (
            <button
              type='button'
              disabled={saving || toggling}
              onClick={() => onEditorModeChange(editorMode === 'edit' ? 'preview' : 'edit')}
              className='inline-flex h-[38px] items-center gap-2 rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-3.5 text-[13px] font-semibold text-[var(--jarvis-text)] transition hover:border-[color:var(--jarvis-border-strong)] hover:bg-[var(--jarvis-card-muted)] disabled:cursor-not-allowed disabled:opacity-60'
            >
              {editorMode === 'edit' ? (
                <EyeIcon className='h-[15px] w-[15px]' />
              ) : (
                <PencilSquareIcon className='h-[15px] w-[15px]' />
              )}
              {editorMode === 'edit' ? 'Preview' : 'Edit'}
            </button>
          )}
          {editorMode === 'edit' && canEdit && (
            <>
              <button
                type='button'
                role='switch'
                aria-label='Always apply'
                aria-checked={alwaysApply}
                disabled={saving}
                onClick={onAlwaysApplyChange}
                className='inline-flex items-center gap-2 text-[13px] text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-60'
              >
                <span>Always Apply</span>
                <span
                  className={`relative h-[22px] w-[38px] flex-shrink-0 rounded-full transition ${
                    alwaysApply ? 'bg-[var(--jarvis-primary)]' : 'bg-[var(--jarvis-border-strong)]'
                  }`}
                >
                  <span
                    className={`absolute left-0 top-0.5 h-[18px] w-[18px] rounded-full bg-white shadow transition-transform ${
                      alwaysApply ? 'translate-x-[18px]' : 'translate-x-0.5'
                    }`}
                  />
                </span>
              </button>
              <SkillCategoryMenu value={draft.category} disabled={saving} onChange={onCategoryChange} />
            </>
          )}
        </div>
      </div>

      <div className='mt-5 min-h-0 min-w-0 flex-1 overflow-auto pr-1'>
        <div className='mb-2.5 text-[13px] font-semibold text-[var(--jarvis-text)]'>Description</div>
        {editorMode === 'edit' && canEdit ? (
          <input
            type='text'
            value={parsedMarkdown.description}
            maxLength={1024}
            aria-label='Skill description'
            placeholder='Describe what this skill does'
            disabled={markdownError !== null}
            onChange={event => onDescriptionChange(event.target.value)}
            className='block h-11 w-full rounded-md border border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] px-3 py-2 text-sm text-[var(--jarvis-text)] outline-none transition placeholder:text-[var(--jarvis-input-placeholder)] focus:border-[var(--jarvis-primary)] focus:bg-[var(--jarvis-input-bg-focus)] focus:ring-2 focus:ring-[color:var(--jarvis-primary)]/20 disabled:cursor-not-allowed disabled:opacity-60'
          />
        ) : (
          <div className='flex h-11 min-w-0 w-full items-center overflow-hidden rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-input-bg)] px-3 text-sm text-[var(--jarvis-muted)]'>
            <span className='min-w-0 flex-1 truncate'>{parsedMarkdown.description || 'No description'}</span>
          </div>
        )}
        {editorMode === 'edit' && canEdit && isSkillMarkdown && (
          <div
            className={`mt-2 text-xs ${
              markdownError ? 'text-[var(--jarvis-danger-text)]' : 'text-[var(--jarvis-muted)]'
            }`}
          >
            {markdownError ? (
              <>Fix the SKILL.md frontmatter to resume Name and Description synchronization.</>
            ) : (
              <>
                Synced with the <strong className='font-semibold text-[var(--jarvis-text)]'>name</strong> and{' '}
                <strong className='font-semibold text-[var(--jarvis-text)]'>description</strong> fields at the top of
                the markdown below.
              </>
            )}
          </div>
        )}

        <div
          className={`mt-5 min-w-0 items-start gap-4 ${supportingFiles.length > 0 ? 'flex flex-col md:flex-row' : 'flex'}`}
        >
          {supportingFiles.length > 0 && (
            <aside className='w-full flex-none overflow-auto rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] p-2 md:w-[216px]'>
              <SkillFileTree files={supportingFiles} selectedPath={selectedPath} onSelect={onSelectFile} />
            </aside>
          )}
          <SkillContentPanel
            skillId={draft.id}
            selectedPath={selectedPath}
            markdown={getSkillMarkdownInput(draft.markdown)}
            markdownBody={parsedMarkdown.body}
            frontmatterSource={frontmatterSource}
            markdownError={markdownError}
            editorMode={editorMode}
            canEdit={canEdit}
            fileMetadata={selectedMetadata}
            onMarkdownChange={onMarkdownChange}
            onEditorModeChange={onEditorModeChange}
          />
        </div>
      </div>

      {canEdit && (
        <div className='mt-2 flex flex-shrink-0 justify-end border-t border-[color:var(--jarvis-border)] pt-5'>
          <div className='flex items-center gap-4'>
            <button
              type='button'
              disabled={saving || toggling}
              onClick={onReset}
              className='px-1.5 py-2.5 text-[13.5px] font-semibold text-[var(--jarvis-muted)] transition hover:text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-60'
            >
              Reset
            </button>
            <button
              type='button'
              disabled={saving || toggling}
              onClick={onSave}
              className='min-w-[76px] rounded-lg bg-[var(--jarvis-primary)] px-[22px] py-2.5 text-[13.5px] font-bold text-white shadow-[0_4px_16px_rgba(124,58,237,0.25)] transition hover:bg-[#6d28d9] disabled:cursor-not-allowed disabled:opacity-60'
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>
      )}
    </section>
  );
};

export default SkillEditorView;
