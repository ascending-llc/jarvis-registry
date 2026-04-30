import formatTimeSince from './formatTimeSince';
import { getJarvisUrl } from './hostInfo';
import { getLocalStorage, setLocalStorage } from './localStorage';
import { cleanupExpiredSessionConfig, getSessionConfig, setSessionConfig } from './sessionConfig';

const UTILS = {
  formatTimeSince,
  getJarvisUrl,
  getLocalStorage,
  setLocalStorage,
  getSessionConfig,
  setSessionConfig,
  cleanupExpiredSessionConfig,
};

export default UTILS;
