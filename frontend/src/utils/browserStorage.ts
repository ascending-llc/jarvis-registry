export type BrowserStorageType = 'local' | 'session';

interface StoredValue<T> {
  value: T;
  expire: number;
}

const getBrowserStorage = (storageType: BrowserStorageType): Storage | null => {
  try {
    return storageType === 'local' ? window.localStorage : window.sessionStorage;
  } catch (_error) {
    return null;
  }
};

export const removeBrowserStorageValue = (storageType: BrowserStorageType, key: string): void => {
  try {
    getBrowserStorage(storageType)?.removeItem(key);
  } catch (_error) {
    // Browser storage is best-effort and must not interrupt application flows.
  }
};

export const setBrowserStorageValue = <T>(
  storageType: BrowserStorageType,
  key: string,
  value: T,
  expireMinutes: number,
): boolean => {
  const storage = getBrowserStorage(storageType);
  if (!storage) return false;

  const data: StoredValue<T> = {
    value,
    expire: Date.now() + expireMinutes * 60 * 1000,
  };

  try {
    storage.setItem(key, JSON.stringify(data));
    return true;
  } catch (_error) {
    return false;
  }
};

export const getBrowserStorageValue = <T>(storageType: BrowserStorageType, key: string): T | null => {
  const storage = getBrowserStorage(storageType);
  if (!storage) return null;

  try {
    const dataStr = storage.getItem(key);
    if (!dataStr) return null;

    const data: Partial<StoredValue<T>> = JSON.parse(dataStr);
    const hasValue = 'value' in data;
    if (!hasValue || typeof data.expire !== 'number' || Date.now() > data.expire) {
      removeBrowserStorageValue(storageType, key);
      return null;
    }

    return data.value ?? null;
  } catch (_error) {
    removeBrowserStorageValue(storageType, key);
    return null;
  }
};

export const takeBrowserStorageValue = <T>(storageType: BrowserStorageType, key: string): T | null => {
  const value = getBrowserStorageValue<T>(storageType, key);
  removeBrowserStorageValue(storageType, key);
  return value;
};
