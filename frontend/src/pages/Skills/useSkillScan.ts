import { useCallback, useEffect, useRef, useState } from 'react';

import SERVICES from '@/services';
import type { SkillScanRequestResult, SkillScanResponse } from '@/services/skill/scanTypes';
import { getScanErrorMessage, getScanHttpStatus, isScanActive, SKILL_SCAN_POLL_INTERVAL_MS } from './skillScan';

type Options = {
  skillId: string | null;
  skillVersion: number | null;
  enabled: boolean;
  canRequestScan: boolean;
  onScanChange: (scan: SkillScanResponse) => void;
};

type ScanState = {
  skillId: string | null;
  scan: SkillScanResponse | null;
  loading: boolean;
  refreshing: boolean;
  requesting: boolean;
  error: string | null;
  requestError: string | null;
  forbidden: boolean;
};

const emptyState: ScanState = {
  skillId: null,
  scan: null,
  loading: false,
  refreshing: false,
  requesting: false,
  error: null,
  requestError: null,
  forbidden: false,
};

const useSkillScan = ({ skillId, skillVersion, enabled, canRequestScan, onScanChange }: Options) => {
  const [state, setState] = useState<ScanState>(emptyState);
  const permissionRef = useRef(canRequestScan);
  const changeRef = useRef(onScanChange);
  const routeRef = useRef({ skillId, skillVersion, enabled });
  permissionRef.current = canRequestScan;
  changeRef.current = onScanChange;
  routeRef.current = { skillId, skillVersion, enabled };
  const controllerRef = useRef<{
    refresh: (minimumVersion?: number) => Promise<void>;
    requestScan: () => Promise<SkillScanRequestResult | null>;
    updateVersion: (version: number) => void;
  } | null>(null);

  useEffect(() => {
    setState({ ...emptyState, skillId, loading: Boolean(skillId && enabled) });
    if (!skillId || !enabled) {
      controllerRef.current = null;
      return;
    }

    let disposed = false;
    let generation = 0;
    let expectedVersion = routeRef.current.skillVersion ?? 0;
    let scan: SkillScanResponse | null = null;
    let fetching = false;
    let submitting = false;
    let refreshQueued = false;
    let forbidden = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const isCurrentRoute = () => !disposed && routeRef.current.skillId === skillId && routeRef.current.enabled;
    const minimumVersion = () =>
      routeRef.current.skillId === skillId
        ? Math.max(expectedVersion, routeRef.current.skillVersion ?? 0)
        : expectedVersion;
    const clearTimer = () => {
      clearTimeout(timer);
      timer = undefined;
    };
    const schedule = () => {
      clearTimer();
      if (isCurrentRoute() && !submitting && !document.hidden && isScanActive(scan?.scanStatus)) {
        timer = setTimeout(() => void refresh(), SKILL_SCAN_POLL_INTERVAL_MS);
      }
    };
    const accept = (response: SkillScanResponse) => {
      if (response.skillId !== skillId) throw new Error('The scan response belongs to a different skill.');
      // A current response can still include a report from a previous version.
      if (response.skillVersion < minimumVersion() || (scan && response.skillVersion < scan.skillVersion)) return false;
      const previousScannedAt = scan?.summary?.scannedAt;
      const scannedAt = response.summary?.scannedAt;
      if (
        response.skillVersion === scan?.skillVersion &&
        previousScannedAt &&
        (!scannedAt || Date.parse(scannedAt) < Date.parse(previousScannedAt))
      ) {
        return false;
      }

      scan = response;
      changeRef.current(response);
      if (isCurrentRoute()) setState(current => ({ ...current, scan: response, error: null }));
      return true;
    };
    const runQueuedRefresh = () => {
      if (!refreshQueued || !isCurrentRoute()) return false;
      refreshQueued = false;
      void refresh();
      return true;
    };
    const refresh = async (version?: number) => {
      if (!isCurrentRoute()) return;
      clearTimer();
      const versionChanged = version !== undefined && version > expectedVersion;
      if (versionChanged) expectedVersion = version;
      if (scan && scan.skillVersion < minimumVersion()) {
        scan = null;
        setState(current => ({ ...current, scan: null, loading: true }));
      }
      if (submitting || fetching) {
        if (submitting || versionChanged) refreshQueued = true;
        return;
      }

      const requestGeneration = ++generation;
      fetching = true;
      setState(current => ({ ...current, loading: !scan, refreshing: Boolean(scan) }));
      let failed = false;
      try {
        const response = await SERVICES.SKILL.getSkillScan(skillId);
        if (!isCurrentRoute() || generation !== requestGeneration) return;
        if (!accept(response) && !scan && !refreshQueued) {
          setState(current => ({ ...current, error: 'Scan status is unavailable for this skill version.' }));
        }
      } catch (error) {
        if (!isCurrentRoute() || generation !== requestGeneration) return;
        failed = true;
        const status = getScanHttpStatus(error);
        if (status === 401 || status === 403 || status === 404) scan = null;
        const retainedScan = scan;
        setState(current => ({
          ...current,
          scan: retainedScan,
          error: getScanErrorMessage(error, 'Failed to load scan report.'),
        }));
      } finally {
        if (isCurrentRoute() && generation === requestGeneration) {
          fetching = false;
          setState(current => ({ ...current, loading: false, refreshing: false }));
          if (!runQueuedRefresh() && !failed) schedule();
        }
      }
    };
    const requestScan = async (): Promise<SkillScanRequestResult | null> => {
      if (!isCurrentRoute() || submitting || forbidden || !permissionRef.current || isScanActive(scan?.scanStatus))
        return null;
      clearTimer();
      generation += 1;
      fetching = false;
      const version = minimumVersion();
      submitting = true;
      setState(current => ({ ...current, requesting: true, loading: false, refreshing: false, requestError: null }));
      try {
        const result = await SERVICES.SKILL.requestSkillScan(skillId);
        const accepted = accept(result.scan);
        return accepted && isCurrentRoute() ? result : null;
      } catch (error) {
        if (!isCurrentRoute() || version < minimumVersion()) return null;
        const status = getScanHttpStatus(error);
        forbidden = status === 401 || status === 403;
        if (status === 401 || status === 404) scan = null;
        const retainedScan = scan;
        const requestForbidden = forbidden;
        setState(current => ({
          ...current,
          scan: retainedScan,
          forbidden: requestForbidden,
          requestError: getScanErrorMessage(error, 'Failed to request a scan.'),
        }));
        return null;
      } finally {
        submitting = false;
        if (isCurrentRoute()) {
          setState(current => ({ ...current, requesting: false }));
          if (!runQueuedRefresh()) schedule();
        }
      }
    };
    const onVisibility = () => {
      clearTimer();
      if (!document.hidden) void refresh();
    };

    controllerRef.current = {
      refresh,
      requestScan,
      updateVersion: version => {
        if (version > expectedVersion) void refresh(version);
      },
    };
    if (!document.hidden) void refresh();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      disposed = true;
      generation += 1;
      clearTimer();
      controllerRef.current = null;
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [enabled, skillId]);

  useEffect(() => {
    if (skillVersion !== null) controllerRef.current?.updateVersion(skillVersion);
  }, [skillVersion]);

  const refresh = useCallback(async (minimumVersion?: number) => {
    await controllerRef.current?.refresh(minimumVersion);
  }, []);
  const requestScan = useCallback(async () => controllerRef.current?.requestScan() ?? null, []);
  const visibleState =
    state.skillId === skillId && enabled ? state : { ...emptyState, skillId, loading: Boolean(skillId && enabled) };
  if (visibleState.scan && skillVersion !== null && visibleState.scan.skillVersion < skillVersion) {
    return { ...visibleState, scan: null, loading: true, refresh, requestScan };
  }
  return { ...visibleState, refresh, requestScan };
};

export default useSkillScan;
