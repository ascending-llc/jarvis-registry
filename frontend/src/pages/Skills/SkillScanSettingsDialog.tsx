import { Dialog } from '@headlessui/react';
import { CheckIcon, XMarkIcon } from '@heroicons/react/24/outline';
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
  const defaultModel = eligibleModels.find(model => model.id === selection?.defaultWorkflowModelSourceId);
  const scanModel = selectedId ? eligibleModels.find(model => model.id === selectedId) : defaultModel;
  const unavailableSelection = selectedId !== '' && !scanModel;
  const changed = selectedId !== (selection?.skillScanModelSourceId ?? '');
  const choices = [
    {
      id: '',
      label: 'No scan model · use the fallback',
      detail: `Falls back to the default workflow model if it runs on Bedrock or Azure. ${
        defaultModel
          ? `Currently: ${defaultModel.displayName} on ${PROVIDER_LABELS[defaultModel.providerType]}.`
          : 'No eligible default model is configured. Scans run without an LLM.'
      }`,
    },
    ...(unavailableSelection
      ? [{ id: selectedId, label: 'Unavailable model', detail: selectedId, disabled: true }]
      : []),
    ...eligibleModels.map(model => ({
      id: model.id,
      label: `${model.displayName} · ${PROVIDER_LABELS[model.providerType]}`,
      detail: 'Chat model',
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
        <Dialog.Panel className='my-auto w-full max-w-[38.5rem] rounded-xl border border-[var(--jarvis-border)] bg-[var(--jarvis-card)] shadow-xl dark:bg-[#131a2a]'>
          <div className='flex items-center gap-3 border-b border-[var(--jarvis-border)] px-4 py-3 sm:px-5'>
            <Dialog.Title className='flex-1 text-lg font-bold text-[var(--jarvis-text-strong)]'>
              Skill scan settings
            </Dialog.Title>
            <span
              title='Only admins can save scan settings'
              className='shrink-0 rounded-md bg-[var(--jarvis-primary-soft)] px-2 py-0.5 text-[11px] font-semibold leading-4 text-[var(--jarvis-primary-text)]'
            >
              Admins only
            </span>
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
          <div className='space-y-4 px-4 py-4 sm:px-5'>
            <section aria-labelledby='skill-scan-model-label'>
              <h3 id='skill-scan-model-label' className='mb-1 text-sm font-semibold text-[var(--jarvis-text-strong)]'>
                Scan model
              </h3>
              <Dialog.Description className='mb-3 text-xs leading-[1.6] text-[var(--jarvis-muted)]'>
                The scanners use this model for their LLM review. Only chat models on AWS Bedrock or Azure OpenAI in
                your own cloud account are eligible, so skill content never leaves it.
              </Dialog.Description>

              {loading && (
                <p role='status' className='mb-3 text-xs text-[var(--jarvis-muted)]'>
                  Loading scan settings...
                </p>
              )}
              {loadError && (
                <div
                  role='alert'
                  className='mb-3 rounded-md bg-[var(--jarvis-danger-soft)] p-3 text-xs text-[var(--jarvis-danger)]'
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
                <>
                  <fieldset
                    disabled={loading || Boolean(loadError) || saving || forbidden}
                    aria-labelledby='skill-scan-model-label'
                    aria-describedby='skill-scan-model-help'
                  >
                    <div className='max-h-72 space-y-1.5 overflow-y-auto p-0.5'>
                      {choices.map(choice => (
                        <label
                          key={choice.id}
                          className={`flex items-start gap-3 rounded-lg border px-4 py-2.5 text-sm ${selectedId === choice.id ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)]' : 'border-[var(--jarvis-border)]'}`}
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
                            className='mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--jarvis-primary)] focus:ring-[var(--jarvis-primary)]'
                          />
                          <span className='min-w-0'>
                            <span className='block break-words font-semibold text-[var(--jarvis-text-strong)]'>
                              {choice.label}
                            </span>
                            <span className='block break-words text-xs text-[var(--jarvis-muted)]'>
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
                    className={`mt-3 flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-[13px] leading-5 ${scanModel ? 'border-emerald-500/30 bg-[var(--jarvis-success-soft)] text-[var(--jarvis-text)]' : 'border-[var(--jarvis-border)] bg-[var(--jarvis-warning-soft)] text-[var(--jarvis-warning-text)]'}`}
                  >
                    {scanModel && (
                      <CheckIcon
                        aria-hidden='true'
                        className='mt-0.5 h-4 w-4 shrink-0 text-[var(--jarvis-success-text)]'
                      />
                    )}
                    <span>
                      {scanModel
                        ? `Scans use ${scanModel.displayName} on ${PROVIDER_LABELS[scanModel.providerType]}. Skill content stays in your ${scanModel.providerType === 'aws_bedrock' ? 'AWS' : 'Azure'} account.`
                        : unavailableSelection
                          ? 'The selected model is missing or no longer eligible. Choose an eligible model or use the fallback.'
                          : 'No eligible default workflow model is configured. Scans run without an LLM.'}
                    </span>
                  </p>
                  {eligibleModels.length === 0 && (
                    <p className='mt-2 text-xs text-[var(--jarvis-muted)]'>
                      No eligible scan models are available. Add an AWS Bedrock or Azure OpenAI chat model in Models to
                      enable LLM scanning.
                    </p>
                  )}
                </>
              )}
            </section>

            <section aria-labelledby='skill-scanners-label'>
              <h3 id='skill-scanners-label' className='mb-2 text-sm font-semibold text-[var(--jarvis-text-strong)]'>
                Scanners
              </h3>
              <p className='rounded-lg border border-[var(--jarvis-border)] px-3 py-2.5 text-xs leading-5 text-[var(--jarvis-muted)]'>
                The scanner inventory is unavailable. See each skill’s Scan details for the scanners used and their
                results.
              </p>
            </section>

            <p className='rounded-lg bg-[var(--jarvis-card-muted)] px-3 py-2.5 text-xs leading-5 text-[var(--jarvis-muted)] dark:bg-white/[0.03]'>
              Changing the scan model can mark existing results as produced by an outdated setup until each affected
              skill is rescanned. There is no bulk rescan.
            </p>

            {saveError && (
              <p role='alert' className='text-xs text-[var(--jarvis-danger)]'>
                {saveError}
              </p>
            )}
            {forbidden && (
              <p role='alert' className='text-xs text-[var(--jarvis-danger)]'>
                You no longer have permission to change scan settings. Close this dialog and contact an administrator.
              </p>
            )}
          </div>
          <div className='flex justify-end gap-2.5 border-t border-[var(--jarvis-border)] px-4 py-3 sm:px-5'>
            <button
              type='button'
              onClick={close}
              disabled={saving}
              className='rounded-lg border border-[var(--jarvis-border-strong)] px-3.5 py-2 text-sm font-semibold text-[var(--jarvis-text)] transition-colors hover:bg-[var(--jarvis-card-muted)] disabled:cursor-not-allowed disabled:opacity-50'
            >
              Cancel
            </button>
            <button
              type='button'
              onClick={() => void save()}
              disabled={!canSave}
              className='rounded-lg border border-transparent bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-[var(--jarvis-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50'
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
