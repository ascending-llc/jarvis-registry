import { Listbox, Switch, Transition } from '@headlessui/react';
import {
  MagnifyingGlassIcon,
  QuestionMarkCircleIcon,
  UserIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { HiOutlineCheck, HiOutlineChevronDown, HiOutlineUsers, HiOutlineShieldCheck } from 'react-icons/hi2';
import { RiGlobalLine, } from "react-icons/ri";
import { FiUserCheck } from "react-icons/fi";


import { useGlobal } from '@/contexts/GlobalContext';
import SERVICES from '@/services';
import type { Principal, PrincipalSearchResult, UpdatePrincipal } from '@/services/acl/type';


type RoleName = 'Owner' | 'Viewer' | 'Editor';

interface UserPermission {
  principalType: string;
  principalId: string;
  name: string;
  email: string;
  role: RoleName;
  source?: string | null;
  idOnTheSource?: string | null;
  isExisting: boolean;
}

export interface ShareModalProps {
  isOpen: boolean;
  onClose: () => void;
  itemName: string;
  resourceId: string;
  resourceType?: string; // defaults to 'mcpServer'
}

const ROLE_ID_MAP: Record<string, RoleName> = {
  owner: 'Owner',
  editor: 'Editor',
  viewer: 'Viewer',
};

const ROLE_NAME_TO_ID: Record<RoleName, string> = {
  Owner: 'owner',
  Editor: 'editor',
  Viewer: 'viewer',
};

const toRoleName = (roleId: string): RoleName => ROLE_ID_MAP[roleId] ?? 'Viewer';

/** Convert a backend Principal to a UI-friendly UserPermission */
const toUserPermission = (principal: Principal): UserPermission => ({
  principalType: principal.type,
  principalId: principal.id,
  name: principal.name || principal.id,
  email: principal.email || '',
  role: toRoleName(principal.accessRoleId || ''),
  source: principal.source,
  idOnTheSource: principal.idOnTheSource,
  isExisting: true,
});

const ShareModal: React.FC<ShareModalProps> = ({
  isOpen,
  onClose,
  itemName,
  resourceId,
  resourceType = 'mcpServer',
}) => {
  const { showToast } = useGlobal();

  // ── State ──
  const [permissions, setPermissions] = useState<UserPermission[]>([]);
  const [removedPermissions, setRemovedPermissions] = useState<UpdatePrincipal[]>([]);
  const [shareWithEveryone, setShareWithEveryone] = useState(false);
  const [initialShareWithEveryone, setInitialShareWithEveryone] = useState(false);
  const [publicRole, setPublicRole] = useState<RoleName>('Viewer');
  const [initialPublicRole, setInitialPublicRole] = useState<RoleName>('Viewer');

  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<PrincipalSearchResult[]>([]);
  const [showSearchDropdown, setShowSearchDropdown] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);

  const [loadingPermissions, setLoadingPermissions] = useState(false);
  const [saving, setSaving] = useState(false);

  const searchContainerRef = useRef<HTMLDivElement>(null);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Escape key ──
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  // ── Click outside search dropdown ──
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target as Node)) {
        setShowSearchDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // ── Fetch permissions on mount ──
  const fetchPermissions = useCallback(async () => {
    setLoadingPermissions(true);
    try {
      const resp = await SERVICES.ACL.getResourcePermissions(resourceType, resourceId);
      const list: UserPermission[] = [];
      const isPublic = Boolean(resp.public);

      let hasPublicRole = false;
      for (const principal of resp.principals || []) {
        if (principal.type === 'public') {
          const role = toRoleName(principal.accessRoleId || '');
          setPublicRole(role);
          setInitialPublicRole(role);
          hasPublicRole = true;
          continue;
        }
        list.push(toUserPermission(principal));
      }
      if (!hasPublicRole) {
        setPublicRole('Viewer');
        setInitialPublicRole('Viewer');
      }

      setPermissions(list);
      setShareWithEveryone(isPublic);
      setInitialShareWithEveryone(isPublic);
    } catch (_error) {
      showToast?.('Failed to load permissions', 'error');
    } finally {
      setLoadingPermissions(false);
    }
  }, [resourceType, resourceId, showToast]);

  useEffect(() => {
    if (isOpen) {
      fetchPermissions();
      // Reset dirty tracking
      setRemovedPermissions([]);
    }
  }, [isOpen, fetchPermissions]);

  // ── Search principals with debounce ──
  useEffect(() => {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    if (!searchQuery.trim()) {
      setSearchResults([]);
      setShowSearchDropdown(false);
      return;
    }

    searchTimerRef.current = setTimeout(async () => {
      setSearchLoading(true);
      try {
        const results = await SERVICES.ACL.searchPrincipals(searchQuery, 10);
        // Filter out principals that already have permissions
        const existingIds = new Set(permissions.map(p => `${p.principalType}:${p.principalId}`));
        const filtered = results.filter(
          r => !existingIds.has(`${r.type || r.principal_type}:${r.id || r.principal_id}`),
        );
        setSearchResults(filtered);
        setShowSearchDropdown(true);
      } catch (_error) {
        setSearchResults([]);
      } finally {
        setSearchLoading(false);
      }
    }, 300);

    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
  }, [searchQuery, permissions]);

  // ── Add a principal from search results ──
  const addPrincipal = (result: PrincipalSearchResult) => {
    const rawType = result.type || result.principal_type || 'user';
    const rawId = result.id || result.principal_id || '';
    const newPerm: UserPermission = {
      principalType: rawType,
      principalId: rawId,
      name: result.name || rawId,
      email: result.email || '',
      role: 'Viewer',
      source: result.source,
      idOnTheSource: result.idOnTheSource,
      isExisting: false,
    };
    setPermissions(prev => [...prev, newPerm]);
    setSearchQuery('');
    setShowSearchDropdown(false);

    // If it was previously removed, undo the removal
    setRemovedPermissions(prev =>
      prev.filter(
        r =>
          !(r.type === rawType && r.id === rawId),
      ),
    );
  };

  // ── Remove a user/group ──
  const removeUser = (principalType: string, principalId: string) => {
    const userToRemove = permissions.find(
      p => p.principalType === principalType && p.principalId === principalId,
    );
    if (!userToRemove) return;

    setPermissions(prev =>
      prev.filter(p => !(p.principalType === principalType && p.principalId === principalId)),
    );

    if (userToRemove.isExisting) {
      setRemovedPermissions(prev => [
        ...prev,
        {
          principal_type: userToRemove.principalType,
          principal_id: userToRemove.principalId,
          type: userToRemove.principalType,
          id: userToRemove.principalId,
          name: userToRemove.name,
          email: userToRemove.email,
          source: userToRemove.source,
          idOnTheSource: userToRemove.idOnTheSource,
          accessRoleId: ROLE_NAME_TO_ID[userToRemove.role],
          isExisting: userToRemove.isExisting,
        },
      ]);
    }
  };

  // ── Change role ──
  const handleRoleChange = (principalType: string, principalId: string, newRole: RoleName) => {
    const userToChange = permissions.find(
      p => p.principalType === principalType && p.principalId === principalId
    );
    if (userToChange?.role === 'Owner' && newRole !== 'Owner') {
      const ownerCount = permissions.filter(p => p.role === 'Owner').length;
      if (ownerCount <= 1) {
        showToast?.('At least one owner is required.', 'error');
        return;
      }
    }

    setPermissions(prev =>
      prev.map(p =>
        p.principalType === principalType && p.principalId === principalId
          ? { ...p, role: newRole }
          : p,
      ),
    );
  };

  // ── Save ──
  const handleSave = async () => {
    setSaving(true);
    try {
      const updated: UpdatePrincipal[] = permissions.map(p => ({
        principal_type: p.principalType,
        principal_id: p.principalId,
        type: p.principalType,
        id: p.principalId,
        name: p.name,
        email: p.email,
        source: p.source,
        idOnTheSource: p.idOnTheSource,
        accessRoleId: ROLE_NAME_TO_ID[p.role],
        isExisting: p.isExisting,
      }));

      const publicChanged = shareWithEveryone !== initialShareWithEveryone || publicRole !== initialPublicRole;
      if (shareWithEveryone && publicChanged) {
        updated.push({
          principal_type: 'public',
          principal_id: '*',
          type: 'public',
          id: '*',
          name: 'public',
          accessRoleId: ROLE_NAME_TO_ID[publicRole],
        });
      }

      await SERVICES.ACL.updateResourcePermissions(resourceType, resourceId, {
        ...(publicChanged ? { public: shareWithEveryone } : {}),
        updated: updated.length > 0 ? updated : undefined,
        removed: removedPermissions.length > 0 ? removedPermissions : undefined,
      });

      showToast?.('Permissions updated successfully', 'success');
      onClose();
    } catch (_error) {
      showToast?.('Failed to update permissions', 'error');
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className='fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm'>
      {/* Modal Container */}
      <div className='mx-4 w-full max-w-4xl max-h-[90vh] overflow-y-auto rounded-xl bg-white dark:bg-gray-800 p-6 shadow-xl'>
        {/* Header */}
        <div className='flex items-center justify-between mb-6'>
          <div className='flex items-center gap-3'>
            <HiOutlineUsers className='h-6 w-6 text-gray-900 dark:text-gray-100' />
            <h2 className='text-xl font-semibold text-gray-900 dark:text-white'>Share {itemName}</h2>
          </div>
          <button
            onClick={onClose}
            className='rounded-full p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-200 transition-colors'
          >
            <XMarkIcon className='h-6 w-6' />
          </button>
        </div>

        {/* Section: User & Group Permissions */}
        <div className='mb-6'>
          <div className='flex items-center gap-2 mb-3'>
            <FiUserCheck className='h-5 w-5 text-gray-600 dark:text-gray-300' />
            <span className='font-medium text-gray-800 dark:text-gray-200'>
              User & Group Permissions ( {permissions.length} )
            </span>
          </div>

          {/* Search Input */}
          <div className='relative mb-4' ref={searchContainerRef}>
            <div className='pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3'>
              <MagnifyingGlassIcon className='h-5 w-5 text-gray-500' />
            </div>
            <input
              type='text'
              placeholder='Search for people or groups by name or email'
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              onFocus={() => {
                if (searchResults.length > 0) setShowSearchDropdown(true);
              }}
              className='block w-full rounded-lg border-gray-200 bg-gray-100 py-3 pl-10 pr-3 text-sm placeholder-gray-500 focus:border-purple-500 focus:bg-white focus:ring-1 focus:ring-purple-500 dark:border-gray-700 dark:bg-gray-700 dark:text-white dark:placeholder-gray-400 dark:focus:bg-gray-800'
            />

            {/* Search Dropdown */}
            {showSearchDropdown && (
              <div className='absolute z-[60] mt-1 w-full rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-xl max-h-60 overflow-auto'>
                {searchLoading ? (
                  <div className='flex items-center justify-center p-4'>
                    <div className='animate-spin rounded-full h-5 w-5 border-b-2 border-purple-600' />
                  </div>
                ) : searchResults.length === 0 ? (
                  <div className='p-4 text-center text-sm text-gray-500 dark:text-gray-400'>
                    No results found
                  </div>
                ) : (
                  <ul className='p-2 space-y-1'>
                    {searchResults.map(result => {
                      const rawType = result.type || result.principal_type;
                      const rawId = result.id || result.principal_id;
                      return (
                        <li
                          key={`${rawType}:${rawId}`}
                          className='flex items-center gap-3 p-3 cursor-pointer rounded-lg border border-gray-200 bg-gray-50 hover:bg-gray-100 dark:border-gray-600 dark:bg-gray-800/50 dark:hover:bg-gray-700 transition-colors'
                          onClick={() => addPrincipal(result)}
                        >
                          <div className='flex h-8 w-8 items-center justify-center rounded-full bg-white dark:bg-gray-700 shadow-sm text-purple-500 dark:text-purple-300'>
                            {rawType === 'group' ? (
                              <HiOutlineUsers className='h-4 w-4' />
                            ) : (
                              <UserIcon className='h-4 w-4' />
                            )}
                          </div>
                          <div className='text-sm flex-1 min-w-0'>
                            <p className='font-semibold text-gray-900 dark:text-gray-100 truncate'>
                              {result.name || rawId}
                            </p>
                            {result.email && (
                              <p className='text-gray-500 dark:text-gray-400 truncate'>
                                {result.email}
                              </p>
                            )}
                          </div>
                          <span className={`text-xs px-2 py-1 rounded-md font-medium capitalize flex-shrink-0 ${rawType === 'group'
                            ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/50 dark:text-purple-300'
                            : 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300'
                            }`}>
                            {rawType}
                          </span>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            )}
          </div>

          {/* Permissions List */}
          <div className='rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800'>
            {loadingPermissions ? (
              <div className='flex items-center justify-center p-8'>
                <div className='animate-spin rounded-full h-6 w-6 border-b-2 border-purple-600' />
              </div>
            ) : permissions.length === 0 ? (
              <div className='p-4 text-center text-sm text-gray-500 dark:text-gray-400'>
                No permissions found.
              </div>
            ) : (
              <ul className='divide-y divide-gray-100 dark:divide-gray-700'>
                {permissions.map(user => {
                  const isLastOwner = user.role === 'Owner' && permissions.filter(p => p.role === 'Owner').length <= 1;
                  return (
                    <li
                      key={`${user.principalType}:${user.principalId}`}
                      className='flex items-center justify-between p-4'
                    >
                      <div className='flex items-center gap-3'>
                        <div className='flex h-10 w-10 items-center justify-center rounded-full bg-purple-50 text-purple-500 dark:bg-purple-900/40 dark:text-purple-300'>
                          {user.principalType === 'group' ? (
                            <HiOutlineUsers className='h-5 w-5' />
                          ) : (
                            <UserIcon className='h-5 w-5' />
                          )}
                        </div>
                        <div className='text-sm'>
                          <p className='font-semibold text-gray-900 dark:text-gray-100'>
                            {user.name}
                          </p>
                          {user.email && (
                            <p className='text-gray-500 dark:text-gray-400'>{user.email}</p>
                          )}
                          {!user.email && (
                            <p className='text-gray-400 dark:text-gray-500 capitalize text-xs'>
                              {user.principalType}
                            </p>
                          )}
                        </div>
                      </div>
                      <div className='flex items-center gap-3'>
                        {/* Role Dropdown */}
                        <Listbox
                          value={user.role}
                          onChange={(value: any) =>
                            handleRoleChange(user.principalType, user.principalId, value)
                          }
                        >
                          <div className='relative'>
                            <Listbox.Button className='relative w-[200px] cursor-default rounded-lg border border-gray-200 bg-transparent dark:border-gray-600 dark:bg-transparent py-2 pl-3 pr-8 text-sm font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-1 focus:ring-gray-300 dark:focus:ring-gray-500 text-left transition-colors'>
                              <span className='block truncate'>MCP Server {user.role}</span>
                              <span className='pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2'>
                                <HiOutlineChevronDown
                                  className='h-4 w-4 text-gray-400 dark:text-gray-400'
                                  aria-hidden='true'
                                />
                              </span>
                            </Listbox.Button>
                            <Transition
                              as={Fragment}
                              leave='transition ease-in duration-100'
                              leaveFrom='opacity-100'
                              leaveTo='opacity-0'
                            >
                              <Listbox.Options className='absolute right-0 z-[60] mt-1 max-h-60 w-[300px] overflow-auto rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 p-1.5 text-sm shadow-xl focus:outline-none'>
                                {[
                                  { value: 'Owner', label: 'MCP Server Owner', desc: 'Full control over MCP servers' },
                                  { value: 'Editor', label: 'MCP Server Editor', desc: 'Can view, use, and edit MCP servers' },
                                  { value: 'Viewer', label: 'MCP Server Viewer', desc: 'Can view and use MCP servers' }
                                ].map(option => (
                                  <Listbox.Option
                                    key={option.value}
                                    className={({ active }) =>
                                      `relative cursor-pointer select-none rounded-md py-2.5 pl-9 pr-3 transition-colors ${active
                                        ? 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-white'
                                        : 'text-gray-700 dark:text-gray-300'
                                      }`
                                    }
                                    value={option.value}
                                  >
                                    {({ selected }) => (
                                      <>
                                        <div className='flex flex-col'>
                                          <span className={`block truncate ${selected ? 'font-medium' : 'font-normal'}`}>
                                            {option.label}
                                          </span>
                                          <span className={`block text-xs mt-0.5 ${selected ? 'text-gray-500 dark:text-gray-400' : 'text-gray-400 dark:text-gray-500'}`}>
                                            {option.desc}
                                          </span>
                                        </div>
                                        {selected ? (
                                          <span className='absolute inset-y-0 left-0 flex items-center pl-3 text-gray-900 dark:text-white'>
                                            <HiOutlineCheck className='h-4 w-4' aria-hidden='true' />
                                          </span>
                                        ) : null}
                                      </>
                                    )}
                                  </Listbox.Option>
                                ))}
                              </Listbox.Options>
                            </Transition>
                          </div>
                        </Listbox>
                        {!isLastOwner ? (
                          <button
                            onClick={() => removeUser(user.principalType, user.principalId)}
                            className='flex h-8 w-8 items-center justify-center rounded-md border border-gray-200 text-gray-500 hover:bg-gray-50 hover:text-gray-700 dark:border-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-300 transition-colors'
                            aria-label='Remove permission'
                          >
                            <XMarkIcon className='h-4 w-4' />
                          </button>
                        ) : (
                          <div className='w-8 h-8' />
                        )}
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        </div>

        {/* Divider */}
        <div className='my-6 border-t border-gray-200 dark:border-gray-700'></div>

        {/* Section: Share with everyone */}
        <div className='flex flex-col gap-4 mb-14'>
          <div className='flex items-center justify-between'>
            <div className='flex items-center gap-2'>
              <RiGlobalLine className={`h-5 w-5 transition-colors ${shareWithEveryone ? 'text-blue-600 dark:text-blue-400' : 'text-gray-600 dark:text-gray-300'}`} />
              <span className='font-semibold text-gray-800 dark:text-gray-200'>
                Share with everyone
              </span>
              <div className='relative group flex items-center'>
                <button className='text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300'>
                  <QuestionMarkCircleIcon className='h-5 w-5' />
                </button>
                <div className='absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block w-72 p-3 bg-white border border-gray-200 dark:bg-gray-800 dark:border-gray-700 text-xs text-gray-700 dark:text-gray-300 rounded-lg shadow-xl z-[70]'>
                  This MCP Server will be available to everyone. Please ensure this MCP Server is suitable for sharing with everyone. Please protect your data.
                </div>
              </div>
            </div>
            <Switch
              checked={shareWithEveryone}
              onChange={setShareWithEveryone}
              className={`${shareWithEveryone ? 'bg-purple-600' : 'bg-gray-300 dark:bg-gray-600'
                } relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-purple-500 focus:ring-offset-2 dark:focus:ring-offset-gray-800`}
            >
              <span className='sr-only'>Enable sharing with everyone</span>
              <span
                className={`${shareWithEveryone ? 'translate-x-6' : 'translate-x-1'
                  } inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
              />
            </Switch>
          </div>

          {shareWithEveryone && (
            <div className='flex items-center justify-between'>
              <div className='flex items-center gap-2'>
                <HiOutlineShieldCheck className='h-5 w-5 text-blue-600 dark:text-blue-400' />
                <span className='font-semibold text-gray-800 dark:text-gray-200'>
                  Permission level for everyone
                </span>
              </div>
              <Listbox
                value={publicRole}
                onChange={setPublicRole}
              >
                <div className='relative'>
                  <Listbox.Button className='relative w-[200px] cursor-default rounded-lg border border-gray-200 bg-transparent dark:border-gray-600 dark:bg-transparent py-2 pl-3 pr-8 text-sm font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-1 focus:ring-gray-300 dark:focus:ring-gray-500 text-left transition-colors'>
                    <span className='block truncate'>MCP Server {publicRole}</span>
                    <span className='pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2'>
                      <HiOutlineChevronDown
                        className='h-4 w-4 text-gray-400 dark:text-gray-400'
                        aria-hidden='true'
                      />
                    </span>
                  </Listbox.Button>
                  <Transition
                    as={Fragment}
                    leave='transition ease-in duration-100'
                    leaveFrom='opacity-100'
                    leaveTo='opacity-0'
                  >
                    <Listbox.Options className='absolute right-0 bottom-full z-[60] mb-1 max-h-60 w-[300px] overflow-auto rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 p-1.5 text-sm shadow-xl focus:outline-none'>
                      {[
                        { value: 'Owner', label: 'MCP Server Owner', desc: 'Full control over MCP servers' },
                        { value: 'Editor', label: 'MCP Server Editor', desc: 'Can view, use, and edit MCP servers' },
                        { value: 'Viewer', label: 'MCP Server Viewer', desc: 'Can view and use MCP servers' }
                      ].map(option => (
                        <Listbox.Option
                          key={option.value}
                          className={({ active }) =>
                            `relative cursor-pointer select-none rounded-md py-2.5 pl-9 pr-3 transition-colors ${active
                              ? 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-white'
                              : 'text-gray-700 dark:text-gray-300'
                            }`
                          }
                          value={option.value}
                        >
                          {({ selected }) => (
                            <>
                              <div className='flex flex-col'>
                                <span className={`block truncate ${selected ? 'font-medium' : 'font-normal'}`}>
                                  {option.label}
                                </span>
                                <span className={`block text-xs mt-0.5 ${selected ? 'text-gray-500 dark:text-gray-400' : 'text-gray-400 dark:text-gray-500'}`}>
                                  {option.desc}
                                </span>
                              </div>
                              {selected ? (
                                <span className='absolute inset-y-0 left-0 flex items-center pl-3 text-gray-900 dark:text-white'>
                                  <HiOutlineCheck className='h-4 w-4' aria-hidden='true' />
                                </span>
                              ) : null}
                            </>
                          )}
                        </Listbox.Option>
                      ))}
                    </Listbox.Options>
                  </Transition>
                </div>
              </Listbox>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className='flex items-center justify-end gap-3'>
          <button
            onClick={onClose}
            disabled={saving}
            className='rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className='inline-flex items-center gap-2 rounded-lg bg-gray-500 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
          >
            {saving && (
              <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-white' />
            )}
            Save Changes
          </button>
        </div>
      </div>
    </div>
  );
};

export default ShareModal;
