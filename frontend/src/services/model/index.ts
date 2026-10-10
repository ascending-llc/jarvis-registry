import API from '@/services/api';
import Request from '@/services/request';

import type * as TYPE from './type';

const getModelSources: (data?: TYPE.ModelSourceListRequest) => Promise<TYPE.ModelSourceListResponse> = async data =>
  await Request.get(API.getModelSources, data);

const getAllModelSources: () => Promise<TYPE.ModelSourceListItem[]> = async () => {
  const firstPage = await getModelSources({ page: 1, per_page: 100 });
  const remainingPages = Array.from(
    { length: Math.max(0, firstPage.pagination.totalPages - 1) },
    (_, index) => index + 2,
  );
  const results = await Promise.all(remainingPages.map(page => getModelSources({ page, per_page: 100 })));
  return [firstPage, ...results].flatMap(result => result.modelSources);
};

const getModelSourceDetail: (id: string) => Promise<TYPE.ModelSourceDetail> = async id =>
  await Request.get(API.getModelSourceDetail(id));

const createModelSource: (data: TYPE.CreateModelSourceRequest) => Promise<TYPE.ModelSourceDetail> = async data =>
  await Request.post(API.createModelSource, data);

const updateModelSource: (id: string, data: TYPE.UpdateModelSourceRequest) => Promise<TYPE.ModelSourceDetail> = async (
  id,
  data,
) => await Request.patch(API.updateModelSource(id), data);

const deleteModelSource: (id: string) => Promise<TYPE.DeleteModelSourceResponse> = async id =>
  await Request.delete(API.deleteModelSource(id));

const getGatewaySelection: () => Promise<TYPE.ModelGatewaySelection> = async () =>
  await Request.get(API.getModelGatewaySelection);

const setDefaultWorkflowModel: (modelSourceId: string) => Promise<TYPE.ModelGatewaySelection> = async modelSourceId =>
  await Request.put(API.setDefaultWorkflowModel, { modelSourceId });

const setEmbeddingModel: (modelSourceId: string) => Promise<TYPE.ModelGatewaySelection> = async modelSourceId =>
  await Request.put(API.setEmbeddingModel, { modelSourceId });

const getEmbeddingReindexJobs: (limit?: number) => Promise<TYPE.EmbeddingReindexJobsResponse> = async (limit = 10) =>
  await Request.get(API.getEmbeddingReindexJobs, { limit });

export default {
  getModelSources,
  getAllModelSources,
  getModelSourceDetail,
  createModelSource,
  updateModelSource,
  deleteModelSource,
  getGatewaySelection,
  setDefaultWorkflowModel,
  setEmbeddingModel,
  getEmbeddingReindexJobs,
};
