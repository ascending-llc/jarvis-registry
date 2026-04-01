import { Dialog, Transition } from '@headlessui/react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import { Fragment } from 'react';
import { FiUserCheck } from 'react-icons/fi';
import { HiOutlineUsers } from 'react-icons/hi2';
import { PermissionList, PrincipalSearch, PublicShare } from './components';
import { type ShareModalProps, useShareModal } from './useShareModal';

export const ShareModal: React.FC<ShareModalProps> = props => {
  const { isOpen, onClose, itemName, resourceType } = props;
  const { search, permissions, publicShare, roles, saving, handleSave } = useShareModal(props);
  const resourceLabel = resourceType === 'agent' ? 'Agent' : 'MCP Server';

  return (
    <Transition.Root show={isOpen} as={Fragment}>
      <Dialog as='div' className='relative z-50' onClose={onClose}>
        <Transition.Child
          as={Fragment}
          enter='ease-out duration-300'
          enterFrom='opacity-0'
          enterTo='opacity-100'
          leave='ease-in duration-200'
          leaveFrom='opacity-100'
          leaveTo='opacity-0'
        >
          <div className='fixed inset-0 bg-black/50 backdrop-blur-sm' />
        </Transition.Child>

        <div className='fixed inset-0 z-10 flex items-center justify-center'>
          <Transition.Child
            as={Fragment}
            enter='ease-out duration-300'
            enterFrom='opacity-0 scale-95'
            enterTo='opacity-100 scale-100'
            leave='ease-in duration-200'
            leaveFrom='opacity-100 scale-100'
            leaveTo='opacity-0 scale-95'
          >
            <Dialog.Panel className='mx-4 w-full max-w-4xl max-h-[90vh] rounded-xl bg-white dark:bg-gray-800 p-6 shadow-xl flex flex-col overflow-hidden'>
              {/* Header */}
              <div className='flex items-center justify-between mb-6'>
                <div className='flex items-center gap-3'>
                  <HiOutlineUsers className='h-6 w-6 text-gray-900 dark:text-gray-100' />
                  <Dialog.Title as='h2' className='text-xl font-semibold text-gray-900 dark:text-white'>
                    Share {itemName}
                  </Dialog.Title>
                </div>
                <button
                  type='button'
                  onClick={onClose}
                  className='rounded-full p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-200 transition-colors'
                >
                  <XMarkIcon className='h-6 w-6' />
                </button>
              </div>

              {/* Section: User & Group Permissions */}
              <div className='flex items-center gap-2 mb-3'>
                <FiUserCheck className='h-5 w-5 text-gray-600 dark:text-gray-300' />
                <span className='font-medium text-gray-800 dark:text-gray-200'>
                  User &amp; Group Permissions ( {permissions.list.length} )
                </span>
              </div>

              <PrincipalSearch search={search} />

              <div className='min-h-0 flex-1 overflow-y-auto pr-1 mb-6'>
                <PermissionList permissions={permissions} roles={roles} resourceLabel={resourceLabel} />

                {/* Divider */}
                <div className='my-6 border-t border-gray-200 dark:border-gray-700' />

                {/* Section: Share with everyone */}
                <PublicShare publicShare={publicShare} roles={roles} resourceLabel={resourceLabel} />

                {/* Footer */}
                <div className='flex items-center justify-end gap-3'>
                  <button
                    type='button'
                    onClick={onClose}
                    disabled={saving}
                    className='rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
                  >
                    Cancel
                  </button>
                  <button
                    type='button'
                    onClick={handleSave}
                    disabled={saving}
                    className='inline-flex items-center gap-2 rounded-lg bg-purple-700 px-4 py-2 text-sm font-semibold text-white hover:bg-purple-800 transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
                  >
                    {saving && <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-white' />}
                    Save Changes
                  </button>
                </div>
              </div>
            </Dialog.Panel>
          </Transition.Child>
        </div>
      </Dialog>
    </Transition.Root>
  );
};

export default ShareModal;
