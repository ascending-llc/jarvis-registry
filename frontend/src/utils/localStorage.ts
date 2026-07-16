export const setLocalStorage = (key: string, value: string, expireMinutes: number) => {
  const now = Date.now();
  const expireTime = now + expireMinutes * 60 * 1000;

  const data = { value: value, expire: expireTime };

  localStorage.setItem(key, JSON.stringify(data));
};

export const getLocalStorage = (key: string) => {
  const dataStr = localStorage.getItem(key);
  if (!dataStr) return null;

  try {
    const data = JSON.parse(dataStr);
    const now = Date.now();

    if (now > data.expire) {
      localStorage.removeItem(key);
      return null;
    }

    return data.value;
  } catch (_error) {
    return null;
  }
};
