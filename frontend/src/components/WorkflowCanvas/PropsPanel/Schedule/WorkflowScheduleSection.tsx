import { Switch } from '@headlessui/react';
import { ArrowPathIcon, ChevronRightIcon, ClockIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { InputField, SelectField } from '@/components/FormFields';
import {
  buildScheduleCronExpression,
  convertInitialInputMode,
  type ScheduleInitialInputMode,
  type ScheduleMode,
  type WorkflowScheduleController,
} from './model';
import { getNextRunPreview } from './nextRun';
import ScheduleInitialInputField from './ScheduleInitialInputField';

interface WorkflowScheduleSectionProps {
  controller: WorkflowScheduleController;
  focusRequestKey: number;
}

const SCHEDULE_MODES: Array<{ label: string; value: ScheduleMode }> = [
  { label: 'Interval', value: 'interval' },
  { label: 'Daily', value: 'daily' },
  { label: 'Weekly', value: 'weekly' },
  { label: 'Monthly', value: 'monthly' },
  { label: 'Cron', value: 'cron' },
];

const WEEKDAYS = [
  { label: 'S', value: 0 },
  { label: 'M', value: 1 },
  { label: 'T', value: 2 },
  { label: 'W', value: 3 },
  { label: 'T', value: 4 },
  { label: 'F', value: 5 },
  { label: 'S', value: 6 },
];

const TIMEZONES = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Paris',
  'Asia/Shanghai',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Australia/Sydney',
];

const DAY_OF_MONTH_OPTIONS = [
  ...Array.from({ length: 31 }, (_, index) => ({ value: String(index + 1), label: String(index + 1) })),
  { value: 'L', label: 'Last day' },
];

const INTERVAL_UNIT_OPTIONS = [
  { value: 'minutes', label: 'Minutes' },
  { value: 'hours', label: 'Hours' },
];

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, hour) => {
  const value = String(hour).padStart(2, '0');
  return { value, label: value };
});

const MINUTE_OPTIONS = Array.from({ length: 60 }, (_, minute) => {
  const value = String(minute).padStart(2, '0');
  return { value, label: value };
});

const _formatNextRun = (value: string | Date, timezone: string): string => {
  const date = value instanceof Date ? value : new Date(value);
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZoneName: 'short',
      timeZone: timezone,
    }).format(date);
  } catch {
    return date.toLocaleString();
  }
};

const WorkflowScheduleSection: React.FC<WorkflowScheduleSectionProps> = ({ controller, focusRequestKey }) => {
  const [open, setOpen] = useState(false);
  const sectionRef = useRef<HTMLDivElement>(null);
  const { draft, schedule, validationErrors } = controller;
  const timezoneOptions = useMemo(
    () =>
      [...new Set([...TIMEZONES, draft.timezone])]
        .filter(Boolean)
        .map(timezone => ({ value: timezone, label: timezone })),
    [draft.timezone],
  );
  const cronExpression = buildScheduleCronExpression(draft);
  const hasPreviewError = Boolean(
    validationErrors.time || validationErrors.cronExpression || validationErrors.timezone,
  );
  const previewNextRun = useMemo(() => {
    if (schedule?.enabled && schedule.nextRunAt) return null;
    if (hasPreviewError) return null;
    return getNextRunPreview(cronExpression, draft.timezone);
  }, [cronExpression, draft.timezone, hasPreviewError, schedule?.enabled, schedule?.nextRunAt]);
  const authoritativeNextRun = schedule?.enabled && schedule.nextRunAt ? schedule.nextRunAt : null;
  const displayedNextRun = authoritativeNextRun ?? previewNextRun;
  const displayedNextRunTimezone = authoritativeNextRun ? (schedule?.timezone ?? draft.timezone) : draft.timezone;
  const [selectedHour = '00', selectedMinute = '00'] = draft.time.split(':');

  useEffect(() => {
    if (focusRequestKey === 0) return;
    setOpen(true);
    const frame = window.requestAnimationFrame(() => {
      sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [focusRequestKey]);

  const setInitialInputMode = (mode: ScheduleInitialInputMode) => {
    const patch = convertInitialInputMode(draft, mode);
    if (patch) controller.setDraftPatch(patch);
  };

  const toggleWeekday = (day: number) => {
    const weekdays = draft.weekdays.includes(day)
      ? draft.weekdays.filter(current => current !== day)
      : [...draft.weekdays, day].sort((left, right) => left - right);
    controller.setDraftPatch({ weekdays });
  };

  const setTimePart = (part: 'hour' | 'minute', value: string) => {
    controller.setDraftPatch({
      time: part === 'hour' ? `${value}:${selectedMinute}` : `${selectedHour}:${value}`,
    });
  };

  const disabled = !controller.canEdit || controller.pageBusy || controller.saving || controller.toggling;
  const summary = controller.loading
    ? 'Loading…'
    : schedule?.enabled
      ? (controller.activeSummary ?? 'Enabled')
      : schedule
        ? 'Disabled'
        : 'Not configured';

  return (
    <div ref={sectionRef} className='border-b border-[color:var(--jarvis-border)]'>
      <div className='flex items-center gap-2 px-4 py-3'>
        <button
          type='button'
          aria-expanded={open}
          onClick={() => setOpen(current => !current)}
          className='flex min-w-0 flex-1 items-center gap-2 text-left'
        >
          <ChevronRightIcon
            className={`h-3.5 w-3.5 flex-shrink-0 text-[var(--jarvis-subtle)] transition-transform ${open ? 'rotate-90' : ''}`}
          />
          <ClockIcon className='h-4 w-4 flex-shrink-0 text-[var(--jarvis-muted)]' />
          <span className='text-xs font-semibold text-[var(--jarvis-text)]'>Schedule</span>
          <span className='ml-auto truncate pl-2 font-mono text-[10px] text-[var(--jarvis-subtle)]'>{summary}</span>
        </button>

        <Switch
          checked={schedule?.enabled === true}
          onChange={enabled => void controller.toggleEnabled(enabled)}
          disabled={!controller.canToggle || controller.isNewWorkflow}
          className={`relative inline-flex h-5 w-9 flex-shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50 ${
            schedule?.enabled ? 'bg-[var(--jarvis-primary)]' : 'bg-[var(--jarvis-border-strong)]'
          }`}
        >
          <span className='sr-only'>Enable workflow schedule</span>
          <span
            className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${
              schedule?.enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
            }`}
          />
        </Switch>
      </div>

      {open && (
        <div className='space-y-4 border-t border-[color:var(--jarvis-border)] px-4 py-4'>
          {controller.isNewWorkflow ? (
            <p className='text-xs leading-relaxed text-[var(--jarvis-muted)]'>
              Save the workflow before configuring a schedule.
            </p>
          ) : controller.loading ? (
            <div className='flex items-center gap-2 text-xs text-[var(--jarvis-muted)]'>
              <ArrowPathIcon className='h-4 w-4 animate-spin' /> Loading schedule…
            </div>
          ) : controller.loadError ? (
            <div className='rounded-md border border-[var(--jarvis-danger)]/40 bg-[var(--jarvis-danger-soft)] p-3'>
              <p className='text-xs text-[var(--jarvis-danger-text)]'>{controller.loadError}</p>
              <button
                type='button'
                onClick={controller.retry}
                className='mt-2 text-xs font-medium text-[var(--jarvis-danger-text)] underline underline-offset-2'
              >
                Retry
              </button>
            </div>
          ) : (
            <>
              {schedule?.enabled !== true && (
                <p className='text-xs leading-relaxed text-[var(--jarvis-muted)]'>
                  Runs only when triggered manually. Turn on to run this workflow on a schedule.
                </p>
              )}

              {!controller.parentEnabled && (
                <div className='rounded-md border border-[var(--jarvis-warning)]/40 bg-[var(--jarvis-warning-soft)] px-3 py-2 text-xs leading-relaxed text-[var(--jarvis-warning-text)]'>
                  {schedule
                    ? 'This workflow is disabled. You can update the saved schedule, but it cannot be enabled yet.'
                    : 'Enable the workflow before creating or enabling its schedule.'}
                </div>
              )}

              <div>
                <label className='mb-1.5 block text-xs font-medium text-[var(--jarvis-text)]'>Repeat</label>
                <div className='grid grid-cols-5 gap-1 rounded-lg bg-[var(--jarvis-card-muted)] p-1'>
                  {SCHEDULE_MODES.map(mode => (
                    <button
                      key={mode.value}
                      type='button'
                      disabled={disabled}
                      onClick={() => controller.setDraftPatch({ mode: mode.value })}
                      className={`min-w-0 rounded-md px-1 py-1.5 text-[10px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                        draft.mode === mode.value
                          ? 'bg-[var(--jarvis-card)] text-[var(--jarvis-text-strong)] shadow-sm'
                          : 'text-[var(--jarvis-muted)] hover:text-[var(--jarvis-text)]'
                      }`}
                    >
                      {mode.label}
                    </button>
                  ))}
                </div>
              </div>

              {draft.mode === 'interval' && (
                <div className='grid grid-cols-[1fr_1.2fr] gap-2'>
                  <InputField
                    label='Every'
                    type='number'
                    min={1}
                    max={draft.intervalUnit === 'minutes' ? 59 : 23}
                    value={draft.intervalValue}
                    onChange={event => controller.setDraftPatch({ intervalValue: event.target.value })}
                    error={validationErrors.interval}
                    disabled={disabled}
                    inputClassName='!text-xs'
                  />
                  <SelectField
                    label='Unit'
                    options={INTERVAL_UNIT_OPTIONS}
                    value={draft.intervalUnit}
                    onChange={intervalUnit =>
                      controller.setDraftPatch({ intervalUnit: intervalUnit as 'minutes' | 'hours' })
                    }
                    disabled={disabled}
                  />
                </div>
              )}

              {draft.mode === 'weekly' && (
                <div>
                  <label className='mb-1.5 block text-xs font-medium text-[var(--jarvis-text)]'>On</label>
                  <div className='grid grid-cols-7 gap-1'>
                    {WEEKDAYS.map((day, index) => (
                      <button
                        key={`${day.label}-${index}`}
                        type='button'
                        disabled={disabled}
                        onClick={() => toggleWeekday(day.value)}
                        className={`h-7 rounded-md border text-[10px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                          draft.weekdays.includes(day.value)
                            ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                            : 'border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] text-[var(--jarvis-muted)]'
                        }`}
                      >
                        {day.label}
                      </button>
                    ))}
                  </div>
                  {validationErrors.weekdays && (
                    <p className='mt-1 text-xs text-[var(--jarvis-danger-text)]'>{validationErrors.weekdays}</p>
                  )}
                </div>
              )}

              {draft.mode === 'monthly' && (
                <SelectField
                  label='Day of month'
                  options={DAY_OF_MONTH_OPTIONS}
                  value={draft.dayOfMonth}
                  onChange={dayOfMonth => controller.setDraftPatch({ dayOfMonth })}
                  disabled={disabled}
                />
              )}

              {['daily', 'weekly', 'monthly'].includes(draft.mode) && (
                <div>
                  <label className='mb-1.5 block text-xs font-medium text-[var(--jarvis-text)]'>At</label>
                  <div className='grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2'>
                    <SelectField
                      ariaLabel='Hour'
                      options={HOUR_OPTIONS}
                      value={selectedHour}
                      onChange={hour => setTimePart('hour', hour)}
                      disabled={disabled}
                    />
                    <span className='font-mono text-sm font-semibold text-[var(--jarvis-muted)]'>:</span>
                    <SelectField
                      ariaLabel='Minute'
                      options={MINUTE_OPTIONS}
                      value={selectedMinute}
                      onChange={minute => setTimePart('minute', minute)}
                      disabled={disabled}
                    />
                  </div>
                  {validationErrors.time && (
                    <p className='mt-1 text-xs text-[var(--jarvis-danger-text)]'>{validationErrors.time}</p>
                  )}
                </div>
              )}

              {draft.mode === 'cron' && (
                <InputField
                  label='CRON expression'
                  value={draft.cronExpression}
                  onChange={event => controller.setDraftPatch({ cronExpression: event.target.value })}
                  helperText='minute · hour · day · month · weekday'
                  error={validationErrors.cronExpression}
                  disabled={disabled}
                  monospace
                  inputClassName='!text-xs'
                />
              )}

              <SelectField
                label='Timezone'
                options={timezoneOptions}
                value={draft.timezone}
                onChange={timezone => controller.setDraftPatch({ timezone })}
                helperText='Use an IANA timezone, such as Asia/Shanghai.'
                error={validationErrors.timezone}
                disabled={disabled}
              />

              <ScheduleInitialInputField
                mode={draft.initialInputMode}
                fields={draft.initialInputFields}
                json={draft.initialInputJson}
                error={validationErrors.initialInput}
                disabled={disabled}
                onModeChange={setInitialInputMode}
                onFieldsChange={initialInputFields => controller.setDraftPatch({ initialInputFields })}
                onJsonChange={initialInputJson => controller.setDraftPatch({ initialInputJson })}
              />

              <div className='rounded-md border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-3 py-2.5'>
                <div className='flex items-center justify-between gap-2 text-[10px] font-semibold uppercase tracking-wide text-[var(--jarvis-subtle)]'>
                  <span>Next run</span>
                  {schedule?.enabled !== true && <span className='font-normal normal-case'>when enabled</span>}
                </div>
                <div className='mt-1 text-xs text-[var(--jarvis-text)]'>
                  {displayedNextRun
                    ? _formatNextRun(displayedNextRun, displayedNextRunTimezone)
                    : 'Fix the schedule settings to preview the next run.'}
                </div>
              </div>

              <p
                className={`text-[10px] leading-relaxed ${
                  controller.isDirty ? 'text-[var(--jarvis-warning-text)]' : 'text-[var(--jarvis-subtle)]'
                }`}
              >
                {controller.isDirty
                  ? 'Schedule changes will be saved with Update.'
                  : 'Schedule configuration is up to date.'}
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
};

export default WorkflowScheduleSection;
