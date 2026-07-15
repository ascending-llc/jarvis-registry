import { isProtectedBrowserPath } from '@/routes';
import {
  getBrowserStorageValue,
  removeBrowserStorageValue,
  setBrowserStorageValue,
  takeBrowserStorageValue,
} from '@/utils/browserStorage';

type AuthReturnToPhase = 'captured' | 'loginStarted';

interface AuthReturnToRecord {
  destination: string;
  phase: AuthReturnToPhase;
}

const AUTH_RETURN_TO_STORAGE_KEY = 'jarvis.auth-return-to';
const AUTH_RETURN_TO_EXPIRE_MINUTES = 15;
const AUTH_RETURN_TO_STORAGE_TYPE = 'session';
let captureSuppressed = false;

const getCurrentDestination = (): string =>
  `${window.location.pathname}${window.location.search}${window.location.hash}`;

const isValidDestination = (destination: string): boolean => {
  if (!destination.startsWith('/') || destination.startsWith('//')) return false;

  try {
    const url = new URL(destination, window.location.origin);
    return url.origin === window.location.origin && isProtectedBrowserPath(url.pathname);
  } catch (_error) {
    return false;
  }
};

const parseRecord = (record: Partial<AuthReturnToRecord> | null): AuthReturnToRecord | null => {
  if (
    !record ||
    typeof record.destination !== 'string' ||
    (record.phase !== 'captured' && record.phase !== 'loginStarted') ||
    !isValidDestination(record.destination)
  ) {
    return null;
  }

  return { destination: record.destination, phase: record.phase };
};

const getRecord = (): AuthReturnToRecord | null => {
  const record = getBrowserStorageValue<Partial<AuthReturnToRecord>>(
    AUTH_RETURN_TO_STORAGE_TYPE,
    AUTH_RETURN_TO_STORAGE_KEY,
  );
  const validRecord = parseRecord(record);
  if (!validRecord && record) clearAuthReturnTo();
  return validRecord;
};

const saveRecord = (record: AuthReturnToRecord): void => {
  setBrowserStorageValue(
    AUTH_RETURN_TO_STORAGE_TYPE,
    AUTH_RETURN_TO_STORAGE_KEY,
    record,
    AUTH_RETURN_TO_EXPIRE_MINUTES,
  );
};

export const captureExplicitAuthReturnTo = (): void => {
  if (captureSuppressed) return;

  const destination = getCurrentDestination();
  if (!isValidDestination(destination)) return;
  saveRecord({ destination, phase: 'captured' });
};

export const capturePassiveAuthReturnTo = (): void => {
  if (captureSuppressed) return;
  if (getRecord()) return;

  const destination = getCurrentDestination();
  if (!isValidDestination(destination)) return;
  saveRecord({ destination, phase: 'captured' });
};

export const markAuthLoginStarted = (): void => {
  const record = getRecord();
  if (!record) return;
  saveRecord({ ...record, phase: 'loginStarted' });
};

export const takeStartedAuthReturnTo = (): string | null => {
  const record = parseRecord(
    takeBrowserStorageValue<Partial<AuthReturnToRecord>>(AUTH_RETURN_TO_STORAGE_TYPE, AUTH_RETURN_TO_STORAGE_KEY),
  );
  return record?.phase === 'loginStarted' ? record.destination : null;
};

export const clearAuthReturnTo = (): void => {
  removeBrowserStorageValue(AUTH_RETURN_TO_STORAGE_TYPE, AUTH_RETURN_TO_STORAGE_KEY);
};

export const suppressAuthReturnToCapture = (): void => {
  captureSuppressed = true;
  clearAuthReturnTo();
};

export const isCurrentDestination = (destination: string): boolean => destination === getCurrentDestination();
