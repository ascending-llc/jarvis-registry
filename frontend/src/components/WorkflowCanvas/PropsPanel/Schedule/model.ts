import type {
  CreateWorkflowScheduleRequest,
  WorkflowSchedule,
  WorkflowScheduleInitialInput,
  WorkflowScheduleJsonValue,
} from '@/services/workflow/type';

export type ScheduleMode = 'interval' | 'daily' | 'weekly' | 'monthly' | 'cron';
export type ScheduleIntervalUnit = 'minutes' | 'hours';
export type ScheduleInitialInputMode = 'fields' | 'json';

export interface ScheduleInputField {
  key: string;
  value: string;
}

export interface WorkflowScheduleDraft {
  mode: ScheduleMode;
  intervalValue: string;
  intervalUnit: ScheduleIntervalUnit;
  time: string;
  weekdays: number[];
  dayOfMonth: string;
  cronExpression: string;
  timezone: string;
  initialInputMode: ScheduleInitialInputMode;
  initialInputFields: ScheduleInputField[];
  initialInputJson: string;
}

export interface ScheduleValidationErrors {
  interval?: string;
  weekdays?: string;
  time?: string;
  cronExpression?: string;
  timezone?: string;
  initialInput?: string;
}

export interface WorkflowScheduleController {
  schedule: WorkflowSchedule | null;
  draft: WorkflowScheduleDraft;
  loading: boolean;
  saving: boolean;
  toggling: boolean;
  pageBusy: boolean;
  loadError: string | null;
  isDirty: boolean;
  isNewWorkflow: boolean;
  parentEnabled: boolean;
  canEdit: boolean;
  canToggle: boolean;
  activeSummary: string | null;
  validationErrors: ScheduleValidationErrors;
  setDraftPatch: (patch: Partial<WorkflowScheduleDraft>) => void;
  retry: () => void;
  saveDraft: () => Promise<boolean>;
  toggleEnabled: (enabled: boolean) => Promise<boolean>;
}

const DEFAULT_TIME = '09:00';
const DEFAULT_CRON = '0 9 * * *';
const DEFAULT_WEEKDAYS = [1, 2, 3, 4, 5];
const FIVE_FIELD_CRON_PATTERN = /^\S+\s+\S+\s+\S+\s+\S+\s+\S+$/;

const _getDefaultTimezone = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
};

const _pad = (value: number): string => String(value).padStart(2, '0');

const _isJsonValue = (value: unknown): value is WorkflowScheduleJsonValue => {
  if (value === null || ['boolean', 'number', 'string'].includes(typeof value)) return true;
  if (Array.isArray(value)) return value.every(_isJsonValue);
  if (typeof value !== 'object') return false;
  return Object.values(value).every(_isJsonValue);
};

const _parseInputFieldValue = (value: string): WorkflowScheduleJsonValue => {
  const trimmed = value.trim();
  if (!trimmed) return '';
  try {
    const parsed: unknown = JSON.parse(trimmed);
    return _isJsonValue(parsed) ? parsed : value;
  } catch {
    return value;
  }
};

const _initialInputToFields = (value: WorkflowScheduleInitialInput | null): ScheduleInputField[] => {
  if (!value || Object.keys(value).length === 0) return [{ key: '', value: '' }];
  return Object.entries(value).map(([key, item]) => ({
    key,
    value: typeof item === 'string' ? item : JSON.stringify(item),
  }));
};

const _parseWeekdays = (value: string): number[] | null => {
  const days = new Set<number>();
  for (const part of value.split(',')) {
    if (/^\d$/.test(part)) {
      const day = Number(part) === 7 ? 0 : Number(part);
      if (day < 0 || day > 6) return null;
      days.add(day);
      continue;
    }
    const match = part.match(/^(\d)-(\d)$/);
    if (!match) return null;
    const start = Number(match[1]);
    const end = Number(match[2]);
    if (start < 0 || end > 6 || start > end) return null;
    for (let day = start; day <= end; day += 1) days.add(day);
  }
  return [...days].sort((left, right) => left - right);
};

const _inferScheduleFields = (cronExpression: string): Partial<WorkflowScheduleDraft> => {
  const [minute, hour, dayOfMonth, month, dayOfWeek] = cronExpression.trim().split(/\s+/);
  if (!minute || !hour || !dayOfMonth || !month || !dayOfWeek) return { mode: 'cron', cronExpression };

  const minuteInterval = minute.match(/^\*\/(\d+)$/);
  if (minuteInterval && hour === '*' && dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    return { mode: 'interval', intervalValue: minuteInterval[1], intervalUnit: 'minutes' };
  }

  const hourInterval = hour.match(/^\*\/(\d+)$/);
  if (minute === '0' && hourInterval && dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    return { mode: 'interval', intervalValue: hourInterval[1], intervalUnit: 'hours' };
  }

  if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && month === '*') {
    const time = `${_pad(Number(hour))}:${_pad(Number(minute))}`;
    if (dayOfMonth === '*' && dayOfWeek === '*') return { mode: 'daily', time };
    if (dayOfMonth === '*') {
      const weekdays = _parseWeekdays(dayOfWeek);
      if (weekdays) return { mode: 'weekly', time, weekdays };
    }
    if (dayOfWeek === '*' && (/^\d+$/.test(dayOfMonth) || dayOfMonth === 'L')) {
      return { mode: 'monthly', time, dayOfMonth };
    }
  }

  return { mode: 'cron', cronExpression };
};

export const buildScheduleCronExpression = (draft: WorkflowScheduleDraft): string => {
  const [hour = '9', minute = '0'] = draft.time.split(':');
  if (draft.mode === 'interval') {
    return draft.intervalUnit === 'minutes' ? `*/${draft.intervalValue} * * * *` : `0 */${draft.intervalValue} * * *`;
  }
  if (draft.mode === 'daily') return `${Number(minute)} ${Number(hour)} * * *`;
  if (draft.mode === 'weekly') {
    return `${Number(minute)} ${Number(hour)} * * ${[...draft.weekdays].sort((a, b) => a - b).join(',')}`;
  }
  if (draft.mode === 'monthly') return `${Number(minute)} ${Number(hour)} ${draft.dayOfMonth} * *`;
  return draft.cronExpression.trim();
};

const _parseInitialInput = (
  draft: WorkflowScheduleDraft,
): { value: WorkflowScheduleInitialInput | null; error?: string } => {
  if (draft.initialInputMode === 'json') {
    const value = draft.initialInputJson.trim();
    if (!value) return { value: null };
    try {
      const parsed: unknown = JSON.parse(value);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || !_isJsonValue(parsed)) {
        return { value: null, error: 'Initial input must be a JSON object' };
      }
      return { value: parsed as WorkflowScheduleInitialInput };
    } catch {
      return { value: null, error: 'Initial input must contain valid JSON' };
    }
  }

  const result: WorkflowScheduleInitialInput = {};
  for (const field of draft.initialInputFields) {
    const key = field.key.trim();
    if (!key && !field.value.trim()) continue;
    if (!key) return { value: null, error: 'Each initial input field needs a key' };
    if (Object.prototype.hasOwnProperty.call(result, key)) {
      return { value: null, error: `Initial input key "${key}" is duplicated` };
    }
    result[key] = _parseInputFieldValue(field.value);
  }
  return { value: Object.keys(result).length > 0 ? result : null };
};

export const createDefaultScheduleDraft = (): WorkflowScheduleDraft => ({
  mode: 'daily',
  intervalValue: '15',
  intervalUnit: 'minutes',
  time: DEFAULT_TIME,
  weekdays: [...DEFAULT_WEEKDAYS],
  dayOfMonth: '1',
  cronExpression: DEFAULT_CRON,
  timezone: _getDefaultTimezone(),
  initialInputMode: 'fields',
  initialInputFields: [{ key: '', value: '' }],
  initialInputJson: '',
});

export const scheduleToDraft = (schedule: WorkflowSchedule): WorkflowScheduleDraft => ({
  ...createDefaultScheduleDraft(),
  ..._inferScheduleFields(schedule.cronExpression),
  cronExpression: schedule.cronExpression,
  timezone: schedule.timezone,
  initialInputFields: _initialInputToFields(schedule.initialInput),
  initialInputJson: schedule.initialInput ? JSON.stringify(schedule.initialInput, null, 2) : '',
});

export const validateScheduleDraft = (draft: WorkflowScheduleDraft): ScheduleValidationErrors => {
  const errors: ScheduleValidationErrors = {};
  if (draft.mode === 'interval') {
    const value = Number(draft.intervalValue);
    const maximum = draft.intervalUnit === 'minutes' ? 59 : 23;
    if (!Number.isInteger(value) || value < 1 || value > maximum) {
      errors.interval = `Enter a whole number between 1 and ${maximum}`;
    }
  }
  if (draft.mode === 'weekly' && draft.weekdays.length === 0) {
    errors.weekdays = 'Select at least one day';
  }
  if (['daily', 'weekly', 'monthly'].includes(draft.mode)) {
    const match = draft.time.match(/^(\d{2}):(\d{2})$/);
    if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) {
      errors.time = 'Enter a valid time';
    }
  }
  const cronExpression = buildScheduleCronExpression(draft);
  if (!FIVE_FIELD_CRON_PATTERN.test(cronExpression)) {
    errors.cronExpression = 'Use a five-field CRON expression';
  }
  if (!draft.timezone.trim()) errors.timezone = 'Timezone is required';
  const initialInput = _parseInitialInput(draft);
  if (initialInput.error) errors.initialInput = initialInput.error;
  return errors;
};

export const scheduleDraftToRequest = (draft: WorkflowScheduleDraft): CreateWorkflowScheduleRequest | null => {
  if (Object.keys(validateScheduleDraft(draft)).length > 0) return null;
  return {
    cronExpression: buildScheduleCronExpression(draft),
    timezone: draft.timezone.trim(),
    initialInput: _parseInitialInput(draft).value,
  };
};

export const getScheduleDraftKey = (draft: WorkflowScheduleDraft): string => {
  const request = scheduleDraftToRequest(draft);
  return request ? JSON.stringify(request) : JSON.stringify(draft);
};

export const getScheduleSummary = (cronExpression: string, timezone: string): string => {
  const draft = { ...createDefaultScheduleDraft(), ..._inferScheduleFields(cronExpression), cronExpression, timezone };
  if (draft.mode === 'interval') {
    const unit = draft.intervalUnit === 'minutes' ? 'min' : 'hr';
    return `Every ${draft.intervalValue} ${unit} · ${timezone}`;
  }
  if (draft.mode === 'daily') return `Daily ${draft.time} · ${timezone}`;
  if (draft.mode === 'weekly') return `Weekly ${draft.time} · ${timezone}`;
  if (draft.mode === 'monthly') return `Monthly ${draft.time} · ${timezone}`;
  return `${cronExpression} · ${timezone}`;
};

export const convertInitialInputMode = (
  draft: WorkflowScheduleDraft,
  mode: ScheduleInitialInputMode,
): Partial<WorkflowScheduleDraft> | null => {
  if (mode === draft.initialInputMode) return {};
  const parsed = _parseInitialInput(draft);
  if (parsed.error) return null;
  if (mode === 'json') {
    return {
      initialInputMode: mode,
      initialInputJson: parsed.value ? JSON.stringify(parsed.value, null, 2) : '',
    };
  }
  return {
    initialInputMode: mode,
    initialInputFields: _initialInputToFields(parsed.value),
  };
};
