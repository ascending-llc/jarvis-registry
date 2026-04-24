import { Dialog, Transition } from '@headlessui/react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import { Fragment } from 'react';
import { FiUserCheck } from 'react-icons/fi';
import { HiOutlineUsers } from 'react-icons/hi2';
import { PermissionList, PrincipalSearch, PublicShare } from './components';
import { type ShareModalProps, useShareModal } from './useShareModal';
import IconButton from '@/components/IconButton';

const RESOURCE_LABELS: Record<string, string> = {
  remoteAgent: 'Agent',
  federation: 'External Registry',
  mcpServer: 'MCP Server',
};

export const ShareModal: React.FC<ShareModalProps> = props => {
  const { isOpen, onClose, itemName, resourceType } = props;
  const { search, permissions, publicShare, roles, saving, handleSave } = useShareModal(props);
  const resourceLabel = RESOURCE_LABELS[resourceType ?? 'mcpServer'] || 'MCP Server';

  return (
    <Transition.Root show={isOpen} as={Fragment}>
      <Dialog as='div' className="relative z-50" onClose={onClose}>
        <Transition.Child
          as={Fragment}
          enter='ease-out duration-300'
          enterFrom='opacity-0'
          enterTo='opacity-100'
          leave='ease-in duration-200'
          leaveFrom='opacity-100'
          leaveTo='opacity-0'
        >
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" />
        </Transition.Child>

        <div className="fixed inset-0 z-10 flex items-center justify-center">
          <Transition.Child
            as={Fragment}
            enter='ease-out duration-300'
            enterFrom='opacity-0 scale-95'
            enterTo='opacity-100 scale-100'
            leave='ease-in duration-200'
            leaveFrom='opacity-100 scale-100'
            leaveTo='opacity-0 scale-95'
          >
            <Dialog.Panel className="mx-4 w-full max-w-4xl max-h-[90vh] rounded-xl bg-[var(--jarvis-card)] bg-[var(--jarvis-card)] p-6 shadow-xl flex flex-col overflow-hidden">
              {/* Header */}
              <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                  <HiOutlineUsers className="h-6 w-6 text-[var(--jarvis-text-strong)]" />
                  <Dialog.Title as='h2' className="text-xl font-semibold text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)]">
                    Share {itemName}
                  </Dialog.Title>
                </div>
                <IconButton
                  ariaLabel="Close"
                  tooltip="Close"
                  onClick={onClose}
                  size="card"
                  className="text-[var(--jarvis-muted)] hover:text-[var(--jarvis-icon-hover)] border-none bg-transparent hover:bg-transparent shadow-none"
                >
                  <XMarkIcon className="h-6 w-6" />
                </IconButton>
              </div>

              {/* Section: User & Group Permissions */}
              <div className="flex items-center gap-2 mb-3">
                <FiUserCheck className="h-5 w-5 text-[var(--jarvis-muted)] text-[var(--jarvis-text)]" />
                <span className="font-medium text-[var(--jarvis-text)] text-[var(--jarvis-text)]">
                  User &amp; Group Permissions ( {permissions.list.length} )
                </span>
              </div>

              <PrincipalSearch search={search} />

              <div className="min-h-0 flex-1 overflow-y-auto pr-1 mb-6">
                <PermissionList permissions={permissions} roles={roles} resourceLabel={resourceLabel} />

                {/* Divider */}
                <div className="my-6 border-t border-[color:var(--jarvis-border)] border-[color:var(--jarvis-border)]" />

                {/* Section: Share with everyone */}
                <PublicShare publicShare={publicShare} roles={roles} resourceLabel={resourceLabel} />

                {/* Footer */}
                <div className="flex items-center justify-end gap-3">
                  <button
                    type='button'
                    onClick={onClose}
                    disabled={saving}
                    className="rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-4 py-2 text-sm font-semibold text-[var(--jarvis-text)] hover:bg-[var(--jarvis-bg)] border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] text-[var(--jarvis-text)] hover:bg-[var(--jarvis-card-muted)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Cancel
                  </button>
                  <button
                    type='button'
                    onClick={handleSave}
                    disabled={saving}
                    className="inline-flex items-center gap-2 rounded-lg bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--jarvis-primary)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {saving && <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />}
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
