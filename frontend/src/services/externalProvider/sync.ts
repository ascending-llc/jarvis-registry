import type { AxiosRequestConfig } from 'axios';

import { getBasePathForUrl } from '@/config';
import API from '@/services/api';
import type { FederationSyncJobStatus } from '@/services/federation/type';
import SKILL_SYNC_SOURCE from '@/services/skillSyncSource';

export const getSkillSyncJobAsFederation = async (
  sourceId: string,
  jobId: string,
  config?: AxiosRequestConfig,
): Promise<FederationSyncJobStatus> => {
  const job = await SKILL_SYNC_SOURCE.getSkillSyncJob(sourceId, jobId, config);
  return {
    id: job.id,
    federationId: job.sourceId,
    jobType: job.jobType,
    status: job.status,
    phase: job.phase,
    startedAt: job.startedAt,
    finishedAt: job.finishedAt,
    error: job.error,
  };
};

export const getSkillSyncSourceOauthUrl = (sourceId: string): string =>
  `${getBasePathForUrl()}${API.initiateSkillSyncSourceOauth(sourceId)}`;

export const getSkillSyncSourceCallbackUrl = (): string =>
  `${window.location.origin}${getBasePathForUrl()}${API.skillSyncSourceOauthCallback}`;
