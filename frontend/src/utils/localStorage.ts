import { getBrowserStorageValue, setBrowserStorageValue } from './browserStorage';

export const setLocalStorage = (key: string, value: string, expireMinutes: number) => {
  setBrowserStorageValue('local', key, value, expireMinutes);
};

export const getLocalStorage = (key: string): string | null => getBrowserStorageValue<string>('local', key);
