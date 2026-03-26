export interface PrincipalSearchResult {
  principal_type: 'user' | 'group' | 'role';
  principal_id: string;
  name?: string;
  email?: string;
  accessRoleId?: string;
}

export interface PermissionEntry {
  principalType: 'user' | 'group' | 'role' | 'public';
  principalId: string;
  permBits: number;
  roleId: string;
  grantedAt: string;
  updatedAt: string;
}

export interface GetResourcePermissionsResponse {
  permissions: PermissionEntry[];
}

export interface UpdateResourcePermissionsRequest {
  public?: boolean;
  removed?: {
    principal_type: string;
    principal_id: string;
  }[];
  updated?: {
    principal_type: string;
    principal_id: string;
    accessRoleId?: string;
    perm_bits?: number;
  }[];
}

export interface UpdateResourcePermissionsResponse {
  message: string;
  results: {
    resource_id: string;
  };
}
