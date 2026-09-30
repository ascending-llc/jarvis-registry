const MINUTE_MS = 60_000;
const DAY_MS = 24 * 60 * MINUTE_MS;
const MAX_PREVIEW_DAYS = 1_462;
const FORMATTER_CACHE = new Map<string, Intl.DateTimeFormat>();

interface DateTimeParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
}

interface ParsedCron {
  minutes: number[];
  hours: number[];
  daysOfMonth: Set<number>;
  months: Set<number>;
  daysOfWeek: Set<number>;
  lastDayOfMonth: boolean;
  dayOfMonthWildcard: boolean;
  dayOfWeekWildcard: boolean;
}

const _parseInteger = (value: string): number | null => {
  if (!/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : null;
};

const _parseField = (field: string, minimum: number, maximum: number): number[] | null => {
  const values = new Set<number>();

  for (const part of field.split(',')) {
    const stepParts = part.split('/');
    if (stepParts.length > 2) return null;

    const range = stepParts[0];
    const step = stepParts[1] === undefined ? 1 : _parseInteger(stepParts[1]);
    if (!range || step === null || step < 1) return null;

    let start: number;
    let end: number;
    if (range === '*') {
      start = minimum;
      end = maximum;
    } else if (range.includes('-')) {
      const boundaries = range.split('-');
      if (boundaries.length !== 2) return null;
      const parsedStart = _parseInteger(boundaries[0]);
      const parsedEnd = _parseInteger(boundaries[1]);
      if (parsedStart === null || parsedEnd === null) return null;
      start = parsedStart;
      end = parsedEnd;
    } else {
      const parsedStart = _parseInteger(range);
      if (parsedStart === null) return null;
      start = parsedStart;
      end = stepParts[1] === undefined ? parsedStart : maximum;
    }

    if (start < minimum || end > maximum || start > end) return null;
    for (let value = start; value <= end; value += step) values.add(value);
  }

  return [...values].sort((left, right) => left - right);
};

const _parseCron = (expression: string): ParsedCron | null => {
  const fields = expression.trim().split(/\s+/);
  if (fields.length !== 5) return null;

  const minutes = _parseField(fields[0], 0, 59);
  const hours = _parseField(fields[1], 0, 23);
  const lastDayOfMonth = fields[2] === 'L';
  const daysOfMonth = lastDayOfMonth ? [] : _parseField(fields[2], 1, 31);
  const months = _parseField(fields[3], 1, 12);
  const parsedDaysOfWeek = _parseField(fields[4], 0, 7);
  if (!minutes || !hours || !daysOfMonth || !months || !parsedDaysOfWeek) return null;

  return {
    minutes,
    hours,
    daysOfMonth: new Set(daysOfMonth),
    months: new Set(months),
    daysOfWeek: new Set(parsedDaysOfWeek.map(day => (day === 7 ? 0 : day))),
    lastDayOfMonth,
    dayOfMonthWildcard: fields[2] === '*',
    dayOfWeekWildcard: fields[4] === '*',
  };
};

const _getDateTimeParts = (date: Date, timezone: string): DateTimeParts | null => {
  try {
    let formatter = FORMATTER_CACHE.get(timezone);
    if (!formatter) {
      formatter = new Intl.DateTimeFormat('en-US-u-ca-gregory-nu-latn', {
        timeZone: timezone,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
      });
      FORMATTER_CACHE.set(timezone, formatter);
    }
    const parts = formatter.formatToParts(date);
    const values = new Map(parts.map(part => [part.type, part.value]));
    const result = {
      year: Number(values.get('year')),
      month: Number(values.get('month')),
      day: Number(values.get('day')),
      hour: Number(values.get('hour')),
      minute: Number(values.get('minute')),
    };
    return Object.values(result).every(Number.isInteger) ? result : null;
  } catch {
    return null;
  }
};

const _sameDateTimeParts = (left: DateTimeParts | null, right: DateTimeParts): boolean =>
  Boolean(
    left &&
      left.year === right.year &&
      left.month === right.month &&
      left.day === right.day &&
      left.hour === right.hour &&
      left.minute === right.minute,
  );

const _zonedDateTimeToDate = (parts: DateTimeParts, timezone: string): Date | null => {
  const targetWallTime = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute);
  let timestamp = targetWallTime;

  for (let attempt = 0; attempt < 3; attempt += 1) {
    const currentParts = _getDateTimeParts(new Date(timestamp), timezone);
    if (!currentParts) return null;
    const currentWallTime = Date.UTC(
      currentParts.year,
      currentParts.month - 1,
      currentParts.day,
      currentParts.hour,
      currentParts.minute,
    );
    const adjustment = targetWallTime - currentWallTime;
    if (adjustment === 0) break;
    timestamp += adjustment;
  }

  const result = new Date(timestamp);
  return _sameDateTimeParts(_getDateTimeParts(result, timezone), parts) ? result : null;
};

const _matchesDay = (cron: ParsedCron, year: number, month: number, day: number): boolean => {
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const dayOfMonthMatches = cron.lastDayOfMonth ? day === lastDay : cron.daysOfMonth.has(day);
  const dayOfWeek = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  const dayOfWeekMatches = cron.daysOfWeek.has(dayOfWeek);

  if (cron.dayOfMonthWildcard && cron.dayOfWeekWildcard) return true;
  if (cron.dayOfMonthWildcard) return dayOfWeekMatches;
  if (cron.dayOfWeekWildcard) return dayOfMonthMatches;
  return dayOfMonthMatches || dayOfWeekMatches;
};

export const getNextRunPreview = (expression: string, timezone: string, from = new Date()): Date | null => {
  const cron = _parseCron(expression);
  const currentParts = _getDateTimeParts(from, timezone);
  if (!cron || !currentParts) return null;

  const firstCalendarDay = Date.UTC(currentParts.year, currentParts.month - 1, currentParts.day);

  for (let dayOffset = 0; dayOffset <= MAX_PREVIEW_DAYS; dayOffset += 1) {
    const calendarDay = new Date(firstCalendarDay + dayOffset * DAY_MS);
    const year = calendarDay.getUTCFullYear();
    const month = calendarDay.getUTCMonth() + 1;
    const day = calendarDay.getUTCDate();
    if (!cron.months.has(month) || !_matchesDay(cron, year, month, day)) continue;

    let nextRun: Date | null = null;
    for (const hour of cron.hours) {
      for (const minute of cron.minutes) {
        if (
          dayOffset === 0 &&
          (hour < currentParts.hour || (hour === currentParts.hour && minute <= currentParts.minute))
        ) {
          continue;
        }
        const candidate = _zonedDateTimeToDate({ year, month, day, hour, minute }, timezone);
        if (
          candidate &&
          candidate.getTime() > from.getTime() &&
          (!nextRun || candidate.getTime() < nextRun.getTime())
        ) {
          nextRun = candidate;
        }
      }
    }

    if (nextRun) return nextRun;
  }

  return null;
};
