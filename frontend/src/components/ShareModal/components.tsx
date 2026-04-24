import { Listbox, Switch, Transition } from '@headlessui/react';
import { MagnifyingGlassIcon, QuestionMarkCircleIcon, UserIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment, useRef } from 'react';
import { createPortal } from 'react-dom';
import { HiOutlineCheck, HiOutlineChevronDown, HiOutlineShieldCheck, HiOutlineUsers } from 'react-icons/hi2';
import { RiGlobalLine } from 'react-icons/ri';
import IconButton from '@/components/IconButton';
import type { Role } from '@/services/acl/type';
import type { PrincipalSearchState } from './usePrincipalSearch';
import { getRoleDisplayDesc, getRoleDisplayName, type PermissionsState, type PublicShareState } from './useShareModal';

// ── RoleDropdown ──

const ROLE_DROPDOWN_BUTTON_WIDTH = 280;
const ROLE_DROPDOWN_OPTIONS_WIDTH = 320;

interface RoleDropdownProps {
  value: string;
  onChange: (value: string) => void;
  roles: Role[];
  resourceLabel?: string;
  direction?: 'up' | 'down';
  disabled?: boolean;
}

export const RoleDropdown: React.FC<RoleDropdownProps> = ({
  value,
  onChange,
  roles,
  resourceLabel = 'MCP Server',
  direction = 'down',
  disabled = false,
}) => {
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
              className={`relative rounded-lg border py-2 pl-3 pr-8 text-sm font-medium text-left transition-colors ${
                disabled
                  ? 'cursor-not-allowed border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] text-[var(--jarvis-faint)]'
                  : 'cursor-pointer border-[color:var(--jarvis-border)] bg-transparent text-[var(--jarvis-text)] hover:bg-[var(--jarvis-card-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--jarvis-border-strong)]'
              }`}
              style={{ width: ROLE_DROPDOWN_BUTTON_WIDTH }}
              title={disabled ? 'At least one owner is required' : undefined}
            >
              <span className='block truncate'>
                {resourceLabel} {selectedRoleName}
              </span>
              <span className='pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2'>
                <HiOutlineChevronDown className='h-4 w-4 text-[var(--jarvis-icon)]' aria-hidden='true' />
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
                    className='fixed z-[80] max-h-60 w-[320px] overflow-auto rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] p-1.5 text-sm shadow-xl focus:outline-none'
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
                              ? 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text-strong)]'
                              : 'text-[var(--jarvis-text)]'
                          }`
                        }
                        value={option.accessRoleId}
                      >
                        {({ selected }) => (
                          <>
                            <div className='flex flex-col'>
                              <span className={`block truncate ${selected ? 'font-medium' : 'font-normal'}`}>
                                {resourceLabel} {getRoleDisplayName(option)}
                              </span>
                              <span
                                className={`mt-0.5 block text-xs ${selected ? 'text-[var(--jarvis-muted)]' : 'text-[var(--jarvis-faint)]'}`}
                              >
                                {getRoleDisplayDesc(option)}
                              </span>
                            </div>
                            {selected ? (
                              <span className='absolute inset-y-0 left-0 flex items-center pl-3 text-[var(--jarvis-text-strong)]'>
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
        <MagnifyingGlassIcon className='h-5 w-5 text-[var(--jarvis-icon)]' />
      </div>
      <input
        type='text'
        placeholder='Search for people or groups by name or email'
        value={search.query}
        onChange={e => search.setQuery(e.target.value)}
        onFocus={() => {
          if (search.results.length > 0) search.setShowDropdown(true);
        }}
        className='block w-full rounded-lg border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] py-3 pl-10 pr-3 text-sm text-[var(--jarvis-text)] placeholder-[var(--jarvis-input-placeholder)] focus:border-[var(--jarvis-primary)] focus:bg-[var(--jarvis-input-bg-focus)] focus:ring-1 focus:ring-[var(--jarvis-primary)]'
      />

      {search.showDropdown && (
        <div className='absolute z-[60] mt-1 max-h-60 w-full overflow-auto rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] shadow-xl'>
          {search.loading ? (
            <div className='flex items-center justify-center p-4'>
              <div className='h-5 w-5 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]' />
            </div>
          ) : search.results.length === 0 ? (
            <div className='p-4 text-center text-sm text-[var(--jarvis-muted)]'>No results found</div>
          ) : (
            <ul className='p-2 space-y-1'>
              {search.results.map(result => {
                const rawType = result.principalType || 'user';
                const rawId = result.principalId || '';
                return (
                  <li
                    key={`${rawType}:${rawId}`}
                    className='flex cursor-pointer items-center gap-3 rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-3 transition-colors hover:bg-[var(--jarvis-primary-soft)]'
                    onClick={() => search.select(result)}
                  >
                    <div className='flex h-8 w-8 items-center justify-center rounded-full bg-[var(--jarvis-card)] text-[var(--jarvis-primary-text)] shadow-sm'>
                      {rawType === 'group' ? <HiOutlineUsers className='h-4 w-4' /> : <UserIcon className='h-4 w-4' />}
                    </div>
                    <div className='min-w-0 flex-1 text-sm'>
                      <p className='truncate font-semibold text-[var(--jarvis-text)]'>{result.name || rawId}</p>
                      {result.email && <p className='truncate text-[var(--jarvis-muted)]'>{result.email}</p>}
                    </div>
                    <span
                      className={`text-xs px-2 py-1 rounded-md font-medium capitalize flex-shrink-0 ${
                        rawType === 'group'
                          ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                          : 'bg-[var(--jarvis-info-soft)] text-[var(--jarvis-info-text)]'
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
  resourceLabel?: string;
}

export const PermissionList: React.FC<PermissionListProps> = ({ permissions, roles, resourceLabel = 'MCP Server' }) => {
  const ownerRoleId = roles[roles.length - 1]?.accessRoleId ?? '';

  return (
    <div className='rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)]'>
      {permissions.loading ? (
        <div className='flex items-center justify-center p-8'>
          <div className='h-6 w-6 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]' />
        </div>
      ) : permissions.list.length === 0 ? (
        <div className='p-4 text-center text-sm text-[var(--jarvis-muted)]'>No permissions found.</div>
      ) : (
        <ul className='divide-y divide-[color:var(--jarvis-border)]'>
          {permissions.list.map(user => {
            const isLastOwner =
              user.accessRoleId === ownerRoleId &&
              permissions.list.filter(p => p.accessRoleId === ownerRoleId).length <= 1;

            return (
              <li key={`${user.principalType}:${user.principalId}`} className='flex items-center justify-between p-4'>
                <div className='flex items-center gap-3'>
                  <div className='flex h-10 w-10 items-center justify-center rounded-full bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'>
                    {user.principalType === 'group' ? (
                      <HiOutlineUsers className='h-5 w-5' />
                    ) : (
                      <UserIcon className='h-5 w-5' />
                    )}
                  </div>
                  <div className='text-sm'>
                    <p className='font-semibold text-[var(--jarvis-text)]'>{user.name}</p>
                    {user.email ? (
                      <p className='text-[var(--jarvis-muted)]'>{user.email}</p>
                    ) : (
                      <p className='text-xs capitalize text-[var(--jarvis-faint)]'>{user.principalType}</p>
                    )}
                  </div>
                </div>

                <div className='flex items-center gap-3 flex-shrink-0'>
                  <RoleDropdown
                    value={user.accessRoleId}
                    onChange={(value: string) => permissions.changeRole(user.principalType, user.principalId, value)}
                    roles={roles}
                    resourceLabel={resourceLabel}
                    disabled={isLastOwner}
                  />
                  {!isLastOwner ? (
                    <IconButton
                      ariaLabel='Remove permission'
                      tooltip='Remove'
                      onClick={() => permissions.remove(user.principalType, user.principalId)}
                      size='card'
                      className='text-[var(--jarvis-icon)] hover:text-[var(--jarvis-icon-hover)]'
                    >
                      <XMarkIcon className='h-4 w-4' />
                    </IconButton>
                  ) : (
                    <div className='w-[26px] h-[26px]' />
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
  resourceLabel?: string;
}

export const PublicShare: React.FC<PublicShareProps> = ({ publicShare, roles, resourceLabel = 'MCP Server' }) => {
  return (
    <div className='flex flex-col gap-4 mb-6'>
      <div className='flex items-center justify-between'>
        <div className='flex items-center gap-2'>
          <RiGlobalLine
            className={`h-5 w-5 transition-colors ${publicShare.enabled ? 'text-[var(--jarvis-primary-text)]' : 'text-[var(--jarvis-muted)]'}`}
          />
          <span className='font-semibold text-[var(--jarvis-text)]'>Share with everyone</span>
          <div className='relative group flex items-center'>
            <IconButton
              ariaLabel='Help'
              tooltip='Info'
              as='span'
              size='card'
              className='text-[var(--jarvis-icon)] hover:text-[var(--jarvis-icon-hover)] border-none bg-transparent hover:bg-transparent shadow-none'
            >
              <QuestionMarkCircleIcon className='h-5 w-5' />
            </IconButton>
            <div className='absolute bottom-full left-1/2 z-[70] mb-2 hidden w-72 -translate-x-1/2 rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] p-3 text-xs text-[var(--jarvis-text)] shadow-xl group-hover:block'>
              This {resourceLabel} will be available to everyone. Please ensure this {resourceLabel} is suitable for
              sharing with everyone. Please protect your data.
            </div>
          </div>
        </div>
        <Switch
          checked={publicShare.enabled}
          onChange={publicShare.setEnabled}
          className={`${
            publicShare.enabled ? 'bg-[var(--jarvis-primary)]' : 'bg-[var(--jarvis-faint)]'
          } relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] focus:ring-offset-2`}
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
            <HiOutlineShieldCheck className='h-5 w-5 text-[var(--jarvis-primary-text)]' />
            <span className='font-semibold text-[var(--jarvis-text)]'>Permission level for everyone</span>
          </div>
          <RoleDropdown
            value={publicShare.role}
            onChange={publicShare.setRole}
            roles={roles}
            resourceLabel={resourceLabel}
            direction='up'
          />
        </div>
      )}
    </div>
  );
};
