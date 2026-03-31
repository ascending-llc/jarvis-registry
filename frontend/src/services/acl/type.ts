export interface PrincipalSearchResult {
  principalType: 'user' | 'group' | 'role';
  principalId: string;
  name?: string;
  email?: string;
  accessRoleId?: string;
  source?: string | null;
  idOnTheSource?: string | null;
}

export interface Principal {
  type: string;
  id: string;
  name?: string;
  email?: string;
  avatar?: string;
  source?: string | null;
  idOnTheSource?: string | null;
  accessRoleId?: string | null;
}

export interface Role {
  accessRoleId: string;
  name: string;
  description: string;
  permBits: number;
}

export interface GetResourcePermissionsResponse {
  resourceType: string;
  resourceId: string;
  principals: Principal[];
  public: boolean;
}

export interface UpdatePrincipal {
  principalType: string;
  principalId: string;
  name?: string;
  email?: string;
  avatar?: string;
  source?: string | null;
  idOnTheSource?: string | null;
  accessRoleId?: string | null;
  isExisting?: boolean;
}

export interface UpdateResourcePermissionsRequest {
  public?: boolean;
  removed?: UpdatePrincipal[];
  updated?: UpdatePrincipal[];
}

export interface UpdateResourcePermissionsResponse {
  message: string;
  results: {
    resource_id: string;
  };
}
