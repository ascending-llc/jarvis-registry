import API from '@/services/api';
import Request from '@/services/request';

import type * as TYPE from './type';

const getSkillSyncSources = (params?: TYPE.GetSkillSyncSourcesParams): Promise<TYPE.GetSkillSyncSourcesResponse> =>
  Request.get(API.getSkillSyncSources, params);

const getSkillSyncSource = (sourceId: string): Promise<TYPE.SkillSyncSourceDetail> =>
  Request.get(API.getSkillSyncSourceDetail(sourceId));

const createSkillSyncSource = (data: TYPE.CreateSkillSyncSourceRequest): Promise<TYPE.SkillSyncSourceDetail> =>
  Request.post(API.createSkillSyncSource, data);

const updateSkillSyncSource = (
  sourceId: string,
  data: TYPE.UpdateSkillSyncSourceRequest,
): Promise<TYPE.SkillSyncSourceDetail | TYPE.SkillSyncTriggerResponse> =>
  Request.put(API.updateSkillSyncSource(sourceId), data);

const deleteSkillSyncSource = (sourceId: string): Promise<TYPE.DeleteSkillSyncSourceResponse> =>
  Request.delete(API.deleteSkillSyncSource(sourceId));

function syncSkillSyncSource(sourceId: string, data: { dryRun: true }): Promise<TYPE.SkillSyncTestResponse>;
function syncSkillSyncSource(sourceId: string, data?: { dryRun?: false }): Promise<TYPE.SkillSyncTriggerResponse>;
function syncSkillSyncSource(sourceId: string, data?: TYPE.SkillSyncRequest): Promise<TYPE.SkillSyncResponse> {
  return Request.post(API.syncSkillSyncSource(sourceId), data);
}

const getSkillSyncJob = (
  sourceId: string,
  jobId: string,
  config?: TYPE.GetSkillSyncJobConfig,
): Promise<TYPE.SkillSyncJob> => Request.get(API.getSkillSyncJob(sourceId, jobId), undefined, config);

const SKILL_SYNC_SOURCE = {
  getSkillSyncSources,
  getSkillSyncSource,
  createSkillSyncSource,
  updateSkillSyncSource,
  deleteSkillSyncSource,
  syncSkillSyncSource,
  getSkillSyncJob,
};

export default SKILL_SYNC_SOURCE;
