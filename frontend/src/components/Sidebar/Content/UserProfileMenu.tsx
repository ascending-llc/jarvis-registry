import { Menu, Transition } from '@headlessui/react';
import {
  ArrowRightStartOnRectangleIcon,
  ChevronUpDownIcon,
  KeyIcon,
  QuestionMarkCircleIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment } from 'react';
import { Link } from 'react-router-dom';

import { useAuth } from '@/contexts/AuthContext';

interface UserProfileMenuProps {
  sidebarOpen: boolean;
}

const UserProfileMenu: React.FC<UserProfileMenuProps> = ({ sidebarOpen }) => {
  const { user, logout } = useAuth();

  const getFirstName = () => {
    if (!user?.username) return 'User';
    if (user.username.includes('@')) {
      const namePart = user.username.split('@')[0];
      const first = namePart.split('.')[0];
      return first.charAt(0).toUpperCase() + first.slice(1);
    }
    if (user.username.includes(' ')) return user.username.split(' ')[0];
    if (user.username.includes('.')) {
      const first = user.username.split('.')[0];
      return first.charAt(0).toUpperCase() + first.slice(1);
    }
    return user.username;
  };

  const getUserEmail = () => {
    if (user?.email) return user.email;
    if (user?.username?.includes('@')) return user.username;
    return '';
  };

  const handleLogout = async () => {
    try {
      await logout();
    } catch (error) {
      console.error('Logout failed:', error);
    }
  };

  if (!user) return null;

  return (
    <div className='p-3 border-t border-[color:var(--jarvis-border)] mt-auto'>
      <Menu as='div' className='relative'>
        {({ open }) => (
          <>
            <Menu.Button
              className={`w-full flex items-center rounded-lg p-1.5 transition-all focus:outline-none border ${
                open
                  ? 'bg-[var(--jarvis-card-muted)] border-[color:var(--jarvis-border-strong)]'
                  : 'border-transparent hover:bg-[var(--jarvis-card-muted)]'
              }`}
              title={user.username}
            >
              <div className='h-7 w-7 rounded-full bg-[var(--jarvis-primary-soft)] border border-[color:var(--jarvis-primary-soft-hover)] flex items-center justify-center text-[var(--jarvis-primary-text)] font-semibold flex-shrink-0 text-xs shadow-sm'>
                {getFirstName().charAt(0).toUpperCase()}
              </div>
              <div
                className={`flex flex-1 items-center justify-between overflow-hidden transition-all duration-300 ${
                  !sidebarOpen ? 'max-w-0 opacity-0 ml-0' : 'max-w-xs opacity-100 ml-3'
                }`}
              >
                <div className='flex-1 min-w-0 text-left overflow-hidden'>
                  <div className='text-sm font-medium text-[var(--jarvis-text-strong)] truncate'>{getFirstName()}</div>
                  <div className='text-xs text-[var(--jarvis-muted)] truncate' title={getUserEmail() || undefined}>
                    {getUserEmail() || (user.isAdmin ? 'Admin Access' : 'User Access')}
                  </div>
                </div>
                <ChevronUpDownIcon className='ml-2 h-4 w-4 text-[var(--jarvis-muted)] flex-shrink-0' />
              </div>
            </Menu.Button>

            <Transition
              as={Fragment}
              enter='transition ease-out duration-100'
              enterFrom='transform opacity-0 scale-95'
              enterTo='transform opacity-100 scale-100'
              leave='transition ease-in duration-75'
              leaveFrom='transform opacity-100 scale-100'
              leaveTo='transform opacity-0 scale-95'
            >
              <Menu.Items
                className={`absolute bottom-full mb-2 z-50 origin-bottom rounded-md bg-[var(--jarvis-card)] py-1 shadow-lg ring-1 ring-[color:var(--jarvis-border)] focus:outline-none ${
                  sidebarOpen ? 'w-full left-0' : 'w-48 left-12'
                }`}
              >
                <Menu.Item>
                  {({ active }) => (
                    <Link
                      to='/generate-token'
                      className={`${
                        active
                          ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                          : 'text-[var(--jarvis-text)]'
                      } flex items-center px-4 py-2 text-sm`}
                    >
                      <KeyIcon className='mr-3 h-4 w-4 text-[var(--jarvis-muted)] flex-shrink-0' />
                      Generate Token
                    </Link>
                  )}
                </Menu.Item>

                <Menu.Item>
                  {({ active }) => (
                    <a
                      href={`https://ascendingdc.com/about/contact-us?m=Inquiry%3A+Additional+help+from+Jarvis&p=%2Findustry%2Fai&firstname=${encodeURIComponent(
                        getFirstName(),
                      )}&email=${encodeURIComponent(getUserEmail())}`}
                      target='_blank'
                      rel='noopener noreferrer'
                      className={`${
                        active
                          ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                          : 'text-[var(--jarvis-text)]'
                      } flex items-center px-4 py-2 text-sm`}
                    >
                      <QuestionMarkCircleIcon className='mr-3 h-4 w-4 text-[var(--jarvis-muted)] flex-shrink-0' />
                      Help & FAQ
                    </a>
                  )}
                </Menu.Item>

                <div className='my-1 border-t border-[color:var(--jarvis-border)]' />

                <Menu.Item>
                  {({ active }) => (
                    <button
                      onClick={handleLogout}
                      className={`${
                        active
                          ? 'bg-[var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)]'
                          : 'text-[var(--jarvis-danger-text)]'
                      } flex w-full items-center px-4 py-2 text-sm`}
                    >
                      <ArrowRightStartOnRectangleIcon className='mr-3 h-4 w-4 text-[var(--jarvis-danger-text)] flex-shrink-0' />
                      Sign out
                    </button>
                  )}
                </Menu.Item>
              </Menu.Items>
            </Transition>
          </>
        )}
      </Menu>
    </div>
  );
};

export default UserProfileMenu;
