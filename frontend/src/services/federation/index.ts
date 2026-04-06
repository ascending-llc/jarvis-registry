import API from '@/services/api';
import Request from '@/services/request';
import type * as TYPE from './type';

/**
 * Fetch a paginated list of external federations
 * @param params Optional query parameters to filter by provider type, status, tags, etc.
 */
const getFederations: (params?: TYPE.GetFederationsParams) => Promise<TYPE.GetFederationsResponse> = async params =>
  await Request.get(API.getFederations, params);

/**
 * Fetch the details of a specific external federation by ID
 * @param federationId The UUID or ID of the federation provider
 */
const getFederation: (federationId: string) => Promise<TYPE.Federation> = async federationId =>
  await Request.get(API.getFederationDetail(federationId));

/**
 * Create a new external federation configuration
 * @param data Request body containing provider settings, display name, etc.
 */
const createFederation: (data: TYPE.CreateFederationRequest) => Promise<TYPE.Federation> = async data =>
  await Request.post(API.createFederation, data);

/**
 * Update an existing external federation configuration
 * @param federationId The UUID or ID of the federation provider
 * @param data Updated configuration data (requires version control)
 */
const updateFederation: (federationId: string, data: TYPE.UpdateFederationRequest) => Promise<TYPE.Federation> = async (
  federationId,
  data,
) => await Request.put(API.updateFederation(federationId), data);

/**
 * Delete a specific external federation by ID
 * @param federationId The UUID or ID of the federation provider
 */
const deleteFederation: (federationId: string) => Promise<void> = async federationId =>
  await Request.delete(API.deleteFederation(federationId));

/**
 * Trigger a background sync job for the specified external federation
 * @param federationId The UUID or ID of the federation provider
 * @param data Optional params like forcing a full resync and audit strings
 */
const syncFederation: (federationId: string, data?: { force?: boolean; reason?: string }) => Promise<any> = async (
  federationId,
  data,
) => await Request.post(API.syncFederation(federationId), data);

const FEDERATION = {
  getFederations,
  getFederation,
  createFederation,
  updateFederation,
  deleteFederation,
  syncFederation,
};

export default FEDERATION;
