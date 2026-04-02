import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useGlobal } from '@/contexts/GlobalContext';
import SERVICES from '@/services';
import type { Principal, PrincipalSearchResult, Role, UpdatePrincipal } from '@/services/acl/type';
import { type PrincipalSearchState, usePrincipalSearch } from './usePrincipalSearch';

// ── Types ──

export interface UserPermission {
  principalType: string;
  principalId: string;
  name: string;
  email: string;
  accessRoleId: string;
  source?: string | null;
  idOnTheSource?: string | null;
  isExisting: boolean;
}

export interface ShareModalProps {
  isOpen: boolean;
  onClose: () => void;
  itemName: string;
  resourceId: string;
  resourceType?: string;
}

export interface PermissionsState {
  list: UserPermission[];
  remove: (principalType: string, principalId: string) => void;
  changeRole: (principalType: string, principalId: string, newRoleId: string) => void;
  loading: boolean;
}

export interface PublicShareState {
  enabled: boolean;
  setEnabled: (val: boolean) => void;
  role: string;
  setRole: (val: string) => void;
}

export interface ShareModalState {
  search: PrincipalSearchState;
  permissions: PermissionsState;
  publicShare: PublicShareState;
  roles: Role[];
  saving: boolean;
  handleSave: () => Promise<void>;
}

// ── Utils ──

export const getRoleDisplayName = (role?: Role, rawId?: string) => {
  if (!role) return rawId || 'Unknown Role';
  const nameLabel = role.name.toLowerCase();
  if (nameLabel.includes('owner')) return 'Owner';
  if (nameLabel.includes('editor')) return 'Editor';
  if (nameLabel.includes('viewer')) return 'Viewer';
  return role.name;
};

export const getRoleDisplayDesc = (role?: Role) => {
  if (!role) return '';
  const descLabel = role.description.toLowerCase();
  const nameLabel = role.name.toLowerCase();
  if (descLabel.includes('owner') || nameLabel.includes('owner')) return 'Full control over resources';
  if (descLabel.includes('editor') || nameLabel.includes('editor')) return 'Can view, use, and edit resources';
  if (descLabel.includes('viewer') || nameLabel.includes('viewer')) return 'Can view and use resources';
  return role.description;
};

const toUserPermission = (principal: Principal, defaultRoleId: string): UserPermission => ({
  principalType: principal.type,
  principalId: principal.id,
  name: principal.name || principal.id,
  email: principal.email || '',
  accessRoleId: principal.accessRoleId || defaultRoleId,
  source: principal.source,
  idOnTheSource: principal.idOnTheSource,
  isExisting: true,
});

// ── Hook ──

export const useShareModal = ({
  isOpen,
  onClose,
  resourceId,
  resourceType = 'mcpServer',
}: ShareModalProps): ShareModalState => {
  const { showToast } = useGlobal();

  const [roles, setRoles] = useState<Role[]>([]);
  const [permissions, setPermissions] = useState<UserPermission[]>([]);
  const [removedPermissions, setRemovedPermissions] = useState<UpdatePrincipal[]>([]);

  const [shareWithEveryone, setShareWithEveryone] = useState(false);
  const initialShareRef = useRef(false);
  const [publicRole, setPublicRole] = useState('');
  const initialPublicRoleRef = useRef('');

  const [loadingPermissions, setLoadingPermissions] = useState(false);
  const [saving, setSaving] = useState(false);

  // ── Data fetching ──

  const fetchData = useCallback(async () => {
    setLoadingPermissions(true);
    try {
      const [rolesData, permsData] = await Promise.all([
        SERVICES.ACL.getResourceRoles(resourceType),
        SERVICES.ACL.getResourcePermissions(resourceType, resourceId),
      ]);

      const sortedRoles = [...rolesData].sort((a, b) => a.permBits - b.permBits);
      setRoles(sortedRoles);
      const defaultRoleId = sortedRoles[0]?.accessRoleId ?? '';

      const list: UserPermission[] = [];
      let foundPublicRole = false;

      for (const principal of permsData.principals || []) {
        if (principal.type === 'public') {
          const roleId = principal.accessRoleId || defaultRoleId;
          setPublicRole(roleId);
          initialPublicRoleRef.current = roleId;
          foundPublicRole = true;
          continue;
        }
        list.push(toUserPermission(principal, defaultRoleId));
      }

      if (!foundPublicRole) {
        setPublicRole(defaultRoleId);
        initialPublicRoleRef.current = defaultRoleId;
      }

      const isPublic = Boolean(permsData.public);
      setPermissions(list);
      setShareWithEveryone(isPublic);
      initialShareRef.current = isPublic;
    } catch {
      showToast?.('Failed to load sharing details', 'error');
    } finally {
      setLoadingPermissions(false);
    }
  }, [resourceType, resourceId, showToast]);

  useEffect(() => {
    if (isOpen) {
      fetchData();
      setRemovedPermissions([]);
    }
  }, [isOpen, fetchData]);

  // ── Search (delegated) ──

  const existingKeys = useMemo(
    () => new Set(permissions.map(p => `${p.principalType}:${p.principalId}`)),
    [permissions],
  );

  const addPrincipal = useCallback(
    (result: PrincipalSearchResult) => {
      const rawType = result.principalType || 'user';
      const rawId = result.principalId || '';
      const defaultRoleId = roles[0]?.accessRoleId ?? '';

      setPermissions(prev => [
        ...prev,
        {
          principalType: rawType,
          principalId: rawId,
          name: result.name || rawId,
          email: result.email || '',
          accessRoleId: defaultRoleId,
          source: result.source,
          idOnTheSource: result.idOnTheSource,
          isExisting: false,
        },
      ]);

      setRemovedPermissions(prev => prev.filter(r => !(r.principalType === rawType && r.principalId === rawId)));
    },
    [roles],
  );

  const search = usePrincipalSearch({ isOpen, existingKeys, onSelect: addPrincipal });

  // ── Permission CRUD ──

  const removeUser = useCallback(
    (principalType: string, principalId: string) => {
      const target = permissions.find(p => p.principalType === principalType && p.principalId === principalId);
      if (!target) return;

      setPermissions(prev => prev.filter(p => !(p.principalType === principalType && p.principalId === principalId)));

      if (target.isExisting) {
        setRemovedPermissions(prev => [
          ...prev,
          {
            principalType: target.principalType,
            principalId: target.principalId,
            name: target.name,
            email: target.email,
            source: target.source,
            idOnTheSource: target.idOnTheSource,
            accessRoleId: target.accessRoleId,
            isExisting: target.isExisting,
          },
        ]);
      }
    },
    [permissions],
  );

  const handleRoleChange = useCallback(
    (principalType: string, principalId: string, newRoleId: string) => {
      const ownerRoleId = roles[roles.length - 1]?.accessRoleId ?? '';

      const target = permissions.find(p => p.principalType === principalType && p.principalId === principalId);
      if (target?.accessRoleId === ownerRoleId && newRoleId !== ownerRoleId) {
        const ownerCount = permissions.filter(p => p.accessRoleId === ownerRoleId).length;
        if (ownerCount <= 1) {
          showToast?.('At least one owner is required.', 'error');
          return;
        }
      }

      setPermissions(prev =>
        prev.map(p =>
          p.principalType === principalType && p.principalId === principalId ? { ...p, accessRoleId: newRoleId } : p,
        ),
      );
    },
    [roles, permissions, showToast],
  );

  // ── Escape key ──

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  // ── Save ──

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const ownerRoleId = roles[roles.length - 1]?.accessRoleId ?? '';
      const ownerCount = permissions.filter(p => p.accessRoleId === ownerRoleId).length;
      if (ownerRoleId && ownerCount < 1) {
        showToast?.('At least one owner is required.', 'error');
        return;
      }

      const updated: UpdatePrincipal[] = permissions.map(p => ({
        principalType: p.principalType,
        principalId: p.principalId,
        name: p.name,
        email: p.email,
        source: p.source,
        idOnTheSource: p.idOnTheSource,
        accessRoleId: p.accessRoleId,
        isExisting: p.isExisting,
      }));

      const publicChanged =
        shareWithEveryone !== initialShareRef.current || publicRole !== initialPublicRoleRef.current;

      if (shareWithEveryone && publicChanged) {
        updated.push({
          principalType: 'public',
          principalId: '*',
          name: 'public',
          accessRoleId: publicRole,
        });
      }

      await SERVICES.ACL.updateResourcePermissions(resourceType, resourceId, {
        ...(publicChanged ? { public: shareWithEveryone } : {}),
        updated: updated.length > 0 ? updated : undefined,
        removed: removedPermissions.length > 0 ? removedPermissions : undefined,
      });

      showToast?.('Permissions updated successfully', 'success');
      onClose();
    } catch {
      showToast?.('Failed to update permissions', 'error');
    } finally {
      setSaving(false);
    }
  }, [permissions, roles, shareWithEveryone, publicRole, removedPermissions, resourceType, resourceId, showToast, onClose]);

  return {
    search,
    permissions: {
      list: permissions,
      remove: removeUser,
      changeRole: handleRoleChange,
      loading: loadingPermissions,
    },
    publicShare: {
      enabled: shareWithEveryone,
      setEnabled: setShareWithEveryone,
      role: publicRole,
      setRole: setPublicRole,
    },
    roles,
    saving,
    handleSave,
  };
};
