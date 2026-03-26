import API from '@/services/api';
import Request from '@/services/request';
import type {
  GetResourcePermissionsResponse,
  PrincipalSearchResult,
  UpdateResourcePermissionsRequest,
  UpdateResourcePermissionsResponse,
} from './type';

/**
 * Search for available Principals (users or groups)
 * @param query Fuzzy matching string
 * @param limit Maximum number of results to return
 * @param principalTypes Required types, e.g., ['user', 'group']
 */
export const searchPrincipals = (
  query: string,
  limit?: number,
  principalTypes?: string[],
): Promise<PrincipalSearchResult[]> => {
  return Request.get(API.searchPrincipals, {
    query,
    limit,
    principal_types: principalTypes,
  });
};

/**
 * Get all permissions for a specific resource
 * @param resourceType Type of the resource, e.g., 'server'
 * @param resourceId UUID or ID of the resource
 */
export const getResourcePermissions = (
  resourceType: string,
  resourceId: string,
): Promise<GetResourcePermissionsResponse> => {
  return Request.get(API.getResourcePermissions(resourceType, resourceId));
};

/**
 * Update or delete resource permissions
 * @param resourceType Type of the resource, e.g., 'server'
 * @param resourceId UUID or ID of the resource
 * @param data Content to update (public status, added/updated, and removed permissions)
 */
export const updateResourcePermissions = (
  resourceType: string,
  resourceId: string,
  data: UpdateResourcePermissionsRequest,
): Promise<UpdateResourcePermissionsResponse> => {
  return Request.put(API.updateResourcePermissions(resourceType, resourceId), data);
};

const ACL = {
  searchPrincipals,
  getResourcePermissions,
  updateResourcePermissions,
};

export default ACL;
