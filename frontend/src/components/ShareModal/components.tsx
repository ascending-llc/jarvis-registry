import { Listbox, Switch, Transition } from '@headlessui/react';
import { MagnifyingGlassIcon, QuestionMarkCircleIcon, UserIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment, useRef } from 'react';
import { createPortal } from 'react-dom';
import { HiOutlineCheck, HiOutlineChevronDown, HiOutlineShieldCheck, HiOutlineUsers } from 'react-icons/hi2';
import { RiGlobalLine } from 'react-icons/ri';
import type { Role } from '@/services/acl/type';
import type { PrincipalSearchState } from './usePrincipalSearch';
import { getRoleDisplayDesc, getRoleDisplayName, type PermissionsState, type PublicShareState } from './useShareModal';

// ── RoleDropdown ──

const ROLE_DROPDOWN_BUTTON_WIDTH = 240;
const ROLE_DROPDOWN_OPTIONS_WIDTH = 320;

interface RoleDropdownProps {
  value: string;
  onChange: (value: string) => void;
  roles: Role[];
  direction?: 'up' | 'down';
  disabled?: boolean;
}

export const RoleDropdown: React.FC<RoleDropdownProps> = ({ value, onChange, roles, direction = 'down', disabled = false }) => {
  const selectedRoleName = getRoleDisplayName(
    roles.find(r => r.accessRoleId === value),
    value,
  );
  const buttonRef = useRef<HTMLButtonElement>(null);

  return (
    <Listbox value={value} onChange={onChange} disabled={disabled}>
      {({ open }) => {
        const rect = buttonRef.current?.getBoundingClientRect();
        const left = rect ? Math.max(8, rect.right - ROLE_DROPDOWN_OPTIONS_WIDTH) : 0;
        const top = rect ? (direction === 'up' ? rect.top - 4 : rect.bottom + 4) : 0;

        return (
          <div className='relative'>
            <Listbox.Button
              ref={buttonRef}
              className={`relative w-[240px] rounded-lg border py-2 pl-3 pr-8 text-sm font-medium text-left transition-colors ${
                disabled
                  ? 'cursor-not-allowed border-gray-200 bg-gray-100 text-gray-400 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-500'
                  : 'cursor-default border-gray-200 bg-transparent text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:bg-transparent dark:text-gray-200 dark:hover:bg-gray-700 focus:outline-none focus:ring-1 focus:ring-gray-300 dark:focus:ring-gray-500'
              }`}
              style={{ width: ROLE_DROPDOWN_BUTTON_WIDTH }}
              title={disabled ? 'At least one owner is required' : undefined}
            >
              <span className='block truncate'>MCP Server {selectedRoleName}</span>
              <span className='pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2'>
                <HiOutlineChevronDown className='h-4 w-4 text-gray-400 dark:text-gray-400' aria-hidden='true' />
              </span>
            </Listbox.Button>
            {typeof document !== 'undefined' &&
              createPortal(
                <Transition
                  show={open}
                  as={Fragment}
                  leave='transition ease-in duration-100'
                  leaveFrom='opacity-100'
                  leaveTo='opacity-0'
                >
                  <Listbox.Options
                    className='fixed z-[80] max-h-60 w-[320px] overflow-auto rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 p-1.5 text-sm shadow-xl focus:outline-none'
                    style={{
                      width: ROLE_DROPDOWN_OPTIONS_WIDTH,
                      left,
                      top,
                      transform: direction === 'up' ? 'translateY(-100%)' : undefined,
                    }}
                  >
                    {roles.map(option => (
                      <Listbox.Option
                        key={option.accessRoleId}
                        className={({ active }) =>
                          `relative cursor-pointer select-none rounded-md py-2.5 pl-9 pr-3 transition-colors ${
                            active
                              ? 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-white'
                              : 'text-gray-700 dark:text-gray-300'
                          }`
                        }
                        value={option.accessRoleId}
                      >
                        {({ selected }) => (
                          <>
                            <div className='flex flex-col'>
                              <span className={`block truncate ${selected ? 'font-medium' : 'font-normal'}`}>
                                MCP Server {getRoleDisplayName(option)}
                              </span>
                              <span
                                className={`block text-xs mt-0.5 ${selected ? 'text-gray-500 dark:text-gray-400' : 'text-gray-400 dark:text-gray-500'}`}
                              >
                                {getRoleDisplayDesc(option)}
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
                </Transition>,
                document.body,
              )}
          </div>
        );
      }}
    </Listbox>
  );
};

// ── PrincipalSearch ──

interface PrincipalSearchProps {
  search: PrincipalSearchState;
}

export const PrincipalSearch: React.FC<PrincipalSearchProps> = ({ search }) => {
  return (
    <div className='relative mb-4' ref={search.containerRef}>
      <div className='pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3'>
        <MagnifyingGlassIcon className='h-5 w-5 text-gray-500' />
      </div>
      <input
        type='text'
        placeholder='Search for people or groups by name or email'
        value={search.query}
        onChange={e => search.setQuery(e.target.value)}
        onFocus={() => {
          if (search.results.length > 0) search.setShowDropdown(true);
        }}
        className='block w-full rounded-lg border-gray-200 bg-gray-100 py-3 pl-10 pr-3 text-sm placeholder-gray-500 focus:border-purple-500 focus:bg-white focus:ring-1 focus:ring-purple-500 dark:border-gray-700 dark:bg-gray-700 dark:text-white dark:placeholder-gray-400 dark:focus:bg-gray-800'
      />

      {search.showDropdown && (
        <div className='absolute z-[60] mt-1 w-full rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-xl max-h-60 overflow-auto'>
          {search.loading ? (
            <div className='flex items-center justify-center p-4'>
              <div className='animate-spin rounded-full h-5 w-5 border-b-2 border-purple-600' />
            </div>
          ) : search.results.length === 0 ? (
            <div className='p-4 text-center text-sm text-gray-500 dark:text-gray-400'>No results found</div>
          ) : (
            <ul className='p-2 space-y-1'>
              {search.results.map(result => {
                const rawType = result.principalType || 'user';
                const rawId = result.principalId || '';
                return (
                  <li
                    key={`${rawType}:${rawId}`}
                    className='flex items-center gap-3 p-3 cursor-pointer rounded-lg border border-gray-200 bg-gray-50 hover:bg-gray-100 dark:border-gray-600 dark:bg-gray-800/50 dark:hover:bg-gray-700 transition-colors'
                    onClick={() => search.select(result)}
                  >
                    <div className='flex h-8 w-8 items-center justify-center rounded-full bg-white dark:bg-gray-700 shadow-sm text-purple-500 dark:text-purple-300'>
                      {rawType === 'group' ? <HiOutlineUsers className='h-4 w-4' /> : <UserIcon className='h-4 w-4' />}
                    </div>
                    <div className='text-sm flex-1 min-w-0'>
                      <p className='font-semibold text-gray-900 dark:text-gray-100 truncate'>{result.name || rawId}</p>
                      {result.email && <p className='text-gray-500 dark:text-gray-400 truncate'>{result.email}</p>}
                    </div>
                    <span
                      className={`text-xs px-2 py-1 rounded-md font-medium capitalize flex-shrink-0 ${
                        rawType === 'group'
                          ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/50 dark:text-purple-300'
                          : 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300'
                      }`}
                    >
                      {rawType}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};

// ── PermissionList ──

interface PermissionListProps {
  permissions: PermissionsState;
  roles: Role[];
}

export const PermissionList: React.FC<PermissionListProps> = ({ permissions, roles }) => {
  const ownerRoleId = roles[roles.length - 1]?.accessRoleId ?? '';

  return (
    <div className='max-h-[340px] overflow-y-auto rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800'>
      {permissions.loading ? (
        <div className='flex items-center justify-center p-8'>
          <div className='animate-spin rounded-full h-6 w-6 border-b-2 border-purple-600' />
        </div>
      ) : permissions.list.length === 0 ? (
        <div className='p-4 text-center text-sm text-gray-500 dark:text-gray-400'>No permissions found.</div>
      ) : (
        <ul className='divide-y divide-gray-100 dark:divide-gray-700'>
          {permissions.list.map(user => {
            const isLastOwner =
              user.accessRoleId === ownerRoleId &&
              permissions.list.filter(p => p.accessRoleId === ownerRoleId).length <= 1;

            return (
              <li key={`${user.principalType}:${user.principalId}`} className='flex items-center justify-between p-4'>
                <div className='flex items-center gap-3'>
                  <div className='flex h-10 w-10 items-center justify-center rounded-full bg-purple-50 text-purple-500 dark:bg-purple-900/40 dark:text-purple-300'>
                    {user.principalType === 'group' ? (
                      <HiOutlineUsers className='h-5 w-5' />
                    ) : (
                      <UserIcon className='h-5 w-5' />
                    )}
                  </div>
                  <div className='text-sm'>
                    <p className='font-semibold text-gray-900 dark:text-gray-100'>{user.name}</p>
                    {user.email ? (
                      <p className='text-gray-500 dark:text-gray-400'>{user.email}</p>
                    ) : (
                      <p className='text-gray-400 dark:text-gray-500 capitalize text-xs'>{user.principalType}</p>
                    )}
                  </div>
                </div>

                <div className='flex items-center gap-3'>
                  <RoleDropdown
                    value={user.accessRoleId}
                    onChange={(value: string) => permissions.changeRole(user.principalType, user.principalId, value)}
                    roles={roles}
                    disabled={isLastOwner}
                  />
                  {!isLastOwner ? (
                    <button
                      type='button'
                      onClick={() => permissions.remove(user.principalType, user.principalId)}
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
            );
          })}
        </ul>
      )}
    </div>
  );
};

// ── PublicShare ──

interface PublicShareProps {
  publicShare: PublicShareState;
  roles: Role[];
}

export const PublicShare: React.FC<PublicShareProps> = ({ publicShare, roles }) => {
  return (
    <div className='flex flex-col gap-4 mb-14'>
      <div className='flex items-center justify-between'>
        <div className='flex items-center gap-2'>
          <RiGlobalLine
            className={`h-5 w-5 transition-colors ${publicShare.enabled ? 'text-purple-600 dark:text-purple-400' : 'text-gray-600 dark:text-gray-300'}`}
          />
          <span className='font-semibold text-gray-800 dark:text-gray-200'>Share with everyone</span>
          <div className='relative group flex items-center'>
            <button
              type='button'
              className='text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300'
            >
              <QuestionMarkCircleIcon className='h-5 w-5' />
            </button>
            <div className='absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block w-72 p-3 bg-white border border-gray-200 dark:bg-gray-800 dark:border-gray-700 text-xs text-gray-700 dark:text-gray-300 rounded-lg shadow-xl z-[70]'>
              This MCP Server will be available to everyone. Please ensure this MCP Server is suitable for sharing with
              everyone. Please protect your data.
            </div>
          </div>
        </div>
        <Switch
          checked={publicShare.enabled}
          onChange={publicShare.setEnabled}
          className={`${
            publicShare.enabled ? 'bg-purple-600' : 'bg-gray-300 dark:bg-gray-600'
          } relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-purple-500 focus:ring-offset-2 dark:focus:ring-offset-gray-800`}
        >
          <span className='sr-only'>Enable sharing with everyone</span>
          <span
            className={`${
              publicShare.enabled ? 'translate-x-6' : 'translate-x-1'
            } inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
          />
        </Switch>
      </div>

      {publicShare.enabled && (
        <div className='flex items-center justify-between'>
          <div className='flex items-center gap-2'>
            <HiOutlineShieldCheck className='h-5 w-5 text-purple-600 dark:text-purple-400' />
            <span className='font-semibold text-gray-800 dark:text-gray-200'>Permission level for everyone</span>
          </div>
          <RoleDropdown value={publicShare.role} onChange={publicShare.setRole} roles={roles} direction='up' />
        </div>
      )}
    </div>
  );
};
