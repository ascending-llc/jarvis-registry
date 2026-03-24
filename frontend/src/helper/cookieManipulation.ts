export const getCookieValue = (name: string): string | null => {
  const encodedName = encodeURIComponent(name);
  const cookiePrefix = `${encodedName}=`;
  const cookieEntry = document.cookie
    .split(';')
    .map(cookie => cookie.trim())
    .find(cookie => cookie.startsWith(cookiePrefix));

  if (!cookieEntry) return null;
  return decodeURIComponent(cookieEntry.slice(cookiePrefix.length));
};
