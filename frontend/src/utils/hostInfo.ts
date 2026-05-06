const DEFAULT_JARVIS_URL = 'https://jarvis-demo.ascendingdc.com';

export const getJarvisUrl = (): string => {
  if (window.location.hostname === 'localhost') return DEFAULT_JARVIS_URL;
  return window.location.origin;
};
