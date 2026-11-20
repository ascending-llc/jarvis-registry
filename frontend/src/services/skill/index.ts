import API from '@/services/api';
import Request from '@/services/request';
import type { SkillScanRequestResult, SkillScanResponse } from './scanTypes';
import type * as TYPE from './type';

const getSkillsList = (): Promise<TYPE.SkillListResponse> => Request.get(API.getSkillsList);

const getSkillDetail = (id: string): Promise<TYPE.SkillDetail> => Request.get(API.getSkillDetail(id));

const getSkillFile = (id: string, filePath: string): Promise<TYPE.SkillFileContent> =>
  Request.get(API.getSkillFile(id, filePath));

const createSkill = (data: TYPE.CreateSkillRequest): Promise<TYPE.SkillDetail> => Request.post(API.createSkill, data);

const updateSkill = (id: string, data: TYPE.UpdateSkillRequest): Promise<TYPE.SkillDetail> =>
  Request.patch(API.updateSkill(id), data);

const deleteSkill = (id: string): Promise<void> => Request.delete(API.deleteSkill(id));

const toggleSkillState = (id: string, data: TYPE.ToggleSkillRequest): Promise<TYPE.ToggleSkillResponse> =>
  Request.post(API.toggleSkillState(id), data);

const getSkillScan = (id: string): Promise<SkillScanResponse> => Request.get(API.getSkillScan(id));

const requestSkillScan = async (id: string): Promise<SkillScanRequestResult> => {
  const response = await Request.postWithStatus<SkillScanResponse>(API.requestSkillScan(id));
  if (response.status !== 200 && response.status !== 202) throw new Error('Unexpected scan response status.');
  return { httpStatus: response.status, scan: response.data };
};

export default {
  getSkillScan,
  requestSkillScan,
  getSkillsList,
  getSkillDetail,
  getSkillFile,
  createSkill,
  updateSkill,
  deleteSkill,
  toggleSkillState,
};
