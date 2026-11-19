import { Dialog } from '@headlessui/react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import { useCallback, useEffect, useRef, useState } from 'react';
import { PROVIDER_LABELS, SKILL_SCAN_PROVIDER_TYPES } from '@/pages/Models/constants';
import modelService from '@/services/model';
import type { ModelGatewaySelection, ModelSourceListItem } from '@/services/model/type';
import { getScanErrorMessage, getScanHttpStatus } from './skillScan';

type SkillScanSettingsDialogProps = {
  isOpen: boolean;
  onClose: () => void;
  onSaved: (selection: ModelGatewaySelection) => void;
};

const isScanModel = (model: ModelSourceListItem) =>
  model.mode === 'chat' && SKILL_SCAN_PROVIDER_TYPES.has(model.providerType);

const SkillScanSettingsContent = ({ onClose, onSaved }: Omit<SkillScanSettingsDialogProps, 'isOpen'>) => {
  const [models, setModels] = useState<ModelSourceListItem[] | null>(null);
  const [selection, setSelection] = useState<ModelGatewaySelection | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [forbidden, setForbidden] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [retryModelsOnly, setRetryModelsOnly] = useState(false);
  const mountedRef = useRef(false);
  const requestRef = useRef(0);
  const savingRef = useRef(false);

  const load = useCallback(async (modelsOnly = false) => {
    const requestId = ++requestRef.current;
    const isCurrent = () => mountedRef.current && requestRef.current === requestId;
    setLoading(true);
    setLoadError(null);
    setRetryModelsOnly(modelsOnly);
    try {
      if (modelsOnly) {
        const nextModels = await modelService.getAllModelSources();
        if (!isCurrent()) return;
        setModels(nextModels);
      } else {
        const [nextModels, nextSelection] = await Promise.all([
          modelService.getAllModelSources(),
          modelService.getGatewaySelection(),
        ]);
        if (!isCurrent()) return;
        if (nextSelection.skillScanModelSourceId === undefined) {
          throw new Error('Skill scan model settings are unavailable from the server. Please retry.');
        }
        setModels(nextModels);
        setSelection(nextSelection);
        setSelectedId(nextSelection.skillScanModelSourceId ?? '');
      }
    } catch (error) {
      if (!isCurrent()) return;
      setLoadError(getScanErrorMessage(error, 'Unable to load skill scan settings. Please try again.'));
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    return () => {
      mountedRef.current = false;
      requestRef.current += 1;
    };
  }, [load]);

  const eligibleModels = (models ?? []).filter(isScanModel);
  const unavailableSelection = selectedId !== '' && !eligibleModels.some(model => model.id === selectedId);
  const selectedModel = eligibleModels.find(model => model.id === selectedId);
  const defaultModel = eligibleModels.find(model => model.id === selection?.defaultWorkflowModelSourceId);
  const changed = selectedId !== (selection?.skillScanModelSourceId ?? '');
  const choices = [
    {
      id: '',
      label: 'Use default workflow model',
      detail: defaultModel
        ? `${defaultModel.displayName} · ${PROVIDER_LABELS[defaultModel.providerType]}`
        : 'No eligible default model. Scans run without an LLM.',
    },
    ...(unavailableSelection
      ? [{ id: selectedId, label: 'Unavailable model', detail: selectedId, disabled: true }]
      : []),
    ...eligibleModels.map(model => ({
      id: model.id,
      label: model.displayName,
      detail: `${PROVIDER_LABELS[model.providerType]} · Chat model`,
    })),
  ];
  const canSave =
    models !== null &&
    selection !== null &&
    !loading &&
    !loadError &&
    !saving &&
    !forbidden &&
    !unavailableSelection &&
    changed;

  const close = () => {
    if (!savingRef.current) onClose();
  };

  const save = async () => {
    if (!canSave || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setSaveError(null);
    let updatedSelection: ModelGatewaySelection;
    try {
      updatedSelection = await modelService.setSkillScanModel(selectedId || null);
    } catch (error) {
      if (!mountedRef.current) return;
      setSaveError(getScanErrorMessage(error, 'Unable to save skill scan settings. Please try again.'));
      const status = getScanHttpStatus(error);
      if (status === 403) setForbidden(true);
      if (status === 404 || status === 409) await load(true);
      return;
    } finally {
      if (mountedRef.current) {
        savingRef.current = false;
        setSaving(false);
      }
    }
    if (!mountedRef.current) return;
    onSaved(updatedSelection);
    onClose();
  };

  return (
    <Dialog open onClose={close} className='relative z-50'>
      <div className='fixed inset-0 bg-black/50 backdrop-blur-sm' aria-hidden='true' />
      <div className='fixed inset-0 flex items-start justify-center overflow-y-auto p-4'>
        <Dialog.Panel className='my-auto w-full max-w-lg rounded-lg border border-[var(--jarvis-border)] bg-[var(--jarvis-card)] p-5 shadow-xl'>
          <div className='mb-3 flex items-center justify-between gap-3'>
            <Dialog.Title className='text-base font-semibold text-[var(--jarvis-text-strong)]'>
              Skill scan settings
            </Dialog.Title>
            <button
              type='button'
              onClick={close}
              disabled={saving}
              aria-label='Close scan settings'
              className='rounded-md p-1.5 text-[var(--jarvis-muted)] hover:bg-[var(--jarvis-card-muted)] disabled:opacity-50'
            >
              <XMarkIcon aria-hidden='true' className='h-4 w-4' />
            </button>
          </div>
          <Dialog.Description className='mb-4 text-xs text-[var(--jarvis-text)]'>
            Choose the model used for skill security scanning. Only AWS Bedrock and Azure OpenAI chat models are
            supported, so skill content stays in your cloud account.
          </Dialog.Description>

          {loading && (
            <p role='status' className='mb-4 text-xs text-[var(--jarvis-muted)]'>
              Loading scan settings...
            </p>
          )}
          {loadError && (
            <div
              role='alert'
              className='mb-4 rounded-md bg-[var(--jarvis-danger-soft)] p-3 text-xs text-[var(--jarvis-danger)]'
            >
              <p>{loadError}</p>
              <button
                type='button'
                onClick={() => void load(retryModelsOnly)}
                disabled={loading || saving}
                className='mt-2 font-medium underline disabled:cursor-not-allowed disabled:opacity-50'
              >
                Retry
              </button>
            </div>
          )}

          {models !== null && selection !== null && (
            <div className='mb-4'>
              <fieldset
                disabled={loading || Boolean(loadError) || saving || forbidden}
                aria-describedby='skill-scan-model-help'
              >
                <legend className='mb-2 text-sm font-medium text-[var(--jarvis-text)]'>Scan model</legend>
                <div className='max-h-64 space-y-2 overflow-y-auto p-0.5'>
                  {choices.map(choice => (
                    <label
                      key={choice.id}
                      className={`flex items-start gap-3 rounded-lg border p-3 text-sm ${selectedId === choice.id ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)]' : 'border-[var(--jarvis-border)]'}`}
                    >
                      <input
                        type='radio'
                        name='skill-scan-model'
                        checked={selectedId === choice.id}
                        disabled={'disabled' in choice && choice.disabled}
                        onChange={() => {
                          setSelectedId(choice.id);
                          setSaveError(null);
                        }}
                        className='mt-0.5 shrink-0'
                      />
                      <span className='min-w-0'>
                        <span className='block break-words font-medium text-[var(--jarvis-text-strong)]'>
                          {choice.label}
                        </span>
                        <span className='mt-1 block break-words text-xs text-[var(--jarvis-muted)]'>
                          {choice.detail}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <p
                id='skill-scan-model-help'
                role='status'
                className={`mt-3 rounded-lg px-3 py-2 text-xs ${!selectedId && !defaultModel ? 'bg-[var(--jarvis-warning-soft)] text-[var(--jarvis-warning-text)]' : 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-muted)]'}`}
              >
                {selectedModel
                  ? `Skill scanning uses ${selectedModel.displayName}, independently of the default workflow model.`
                  : selectedId
                    ? 'Choose an eligible model or clear the unavailable selection.'
                    : defaultModel
                      ? `Skill scanning falls back to the default workflow model: ${defaultModel.displayName}.`
                      : 'Scanning runs without an LLM because no eligible default workflow model is configured.'}
              </p>
              {eligibleModels.length === 0 && (
                <p className='mt-2 text-xs text-[var(--jarvis-muted)]'>
                  No eligible scan models are available. Add an AWS Bedrock or Azure OpenAI chat model in Models to
                  enable LLM scanning.
                </p>
              )}
              {unavailableSelection && (
                <p role='alert' className='mt-2 text-xs text-[var(--jarvis-warning-text)]'>
                  The selected model is missing or no longer eligible. Choose another model or select "Use default
                  workflow model" to clear this selection.
                </p>
              )}
              <p className='mt-3 text-xs text-[var(--jarvis-muted)]'>
                Changing the model can mark existing reports as out of date. Run a new scan for each skill to update its
                report.
              </p>
            </div>
          )}

          {saveError && (
            <p role='alert' className='mb-4 text-xs text-[var(--jarvis-danger)]'>
              {saveError}
            </p>
          )}
          {forbidden && (
            <p role='alert' className='mb-4 text-xs text-[var(--jarvis-danger)]'>
              You no longer have permission to change scan settings. Close this dialog and contact an administrator.
            </p>
          )}
          <div className='flex justify-end gap-2'>
            <button
              type='button'
              onClick={close}
              disabled={saving}
              className='rounded-md border border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-3 py-1.5 text-xs font-medium text-[var(--jarvis-text)] transition-colors hover:bg-[var(--jarvis-surface)] disabled:cursor-not-allowed disabled:opacity-50'
            >
              Cancel
            </button>
            <button
              type='button'
              onClick={() => void save()}
              disabled={!canSave}
              className='rounded-md border border-transparent bg-[var(--jarvis-primary)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50'
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </Dialog.Panel>
      </div>
    </Dialog>
  );
};

const SkillScanSettingsDialog = ({ isOpen, onClose, onSaved }: SkillScanSettingsDialogProps) =>
  isOpen ? <SkillScanSettingsContent onClose={onClose} onSaved={onSaved} /> : null;

export default SkillScanSettingsDialog;
