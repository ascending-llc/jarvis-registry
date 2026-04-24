import { Dialog, Transition } from '@headlessui/react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment, useState } from 'react';

import Content from './Content';
import TokenModal from './TokenModal';

interface SidebarProps {
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
}

const Sidebar: React.FC<SidebarProps> = ({ sidebarOpen, setSidebarOpen }) => {
  const [showTokenModal, setShowTokenModal] = useState(false);
  const [tokenData, setTokenData] = useState<any>(null);

  return (
    <>
      {/* Mobile sidebar only */}
      {window.innerWidth < 768 && (
        <Transition.Root show={sidebarOpen} as={Fragment}>
          <Dialog as='div' className="relative z-50" onClose={setSidebarOpen}>
            <Transition.Child
              as={Fragment}
              enter='transition-opacity ease-linear duration-300'
              enterFrom='opacity-0'
              enterTo='opacity-100'
              leave='transition-opacity ease-linear duration-300'
              leaveFrom='opacity-100'
              leaveTo='opacity-0'
            >
              <div className="fixed inset-0 bg-[var(--jarvis-overlay)]" />
            </Transition.Child>

            <div className="fixed inset-0 flex">
              <Transition.Child
                as={Fragment}
                enter='transition ease-in-out duration-300 transform'
                enterFrom='-translate-x-full'
                enterTo='translate-x-0'
                leave='transition ease-in-out duration-300 transform'
                leaveFrom='translate-x-0'
                leaveTo='-translate-x-full'
              >
                <Dialog.Panel className="relative mr-16 flex w-full max-w-xs flex-1">
                  <Transition.Child
                    as={Fragment}
                    enter='ease-in-out duration-300'
                    enterFrom='opacity-0'
                    enterTo='opacity-100'
                    leave='ease-in-out duration-300'
                    leaveFrom='opacity-100'
                    leaveTo='opacity-0'
                  >
                    <div className="absolute left-full top-0 flex w-16 justify-center pt-5">
                      <button className="-m-2.5 p-2.5" onClick={() => setSidebarOpen(false)} aria-label='Close sidebar'>
                        <XMarkIcon className="h-6 w-6 text-white" />
                      </button>
                    </div>
                  </Transition.Child>

                  <div className="flex grow flex-col gap-y-5 overflow-y-auto bg-[var(--jarvis-card)] bg-[var(--jarvis-card)] border-r border-[color:var(--jarvis-border)] border-[color:var(--jarvis-border)]">
                    <Content setSidebarOpen={setSidebarOpen} />
                  </div>
                </Dialog.Panel>
              </Transition.Child>
            </div>
          </Dialog>
        </Transition.Root>
      )}

      {/* Desktop sidebar only */}
      {window.innerWidth >= 768 && (
        <Transition show={sidebarOpen} as={Fragment}>
          <Transition.Child
            as={Fragment}
            enter='transition ease-in-out duration-300 transform'
            enterFrom='-translate-x-full'
            enterTo='translate-x-0'
            leave='transition ease-in-out duration-300 transform'
            leaveFrom='translate-x-0'
            leaveTo='-translate-x-full'
          >
            <div className="fixed left-0 top-16 bottom-0 z-40 w-64 lg:w-72 xl:w-80 bg-[var(--jarvis-card)] bg-[var(--jarvis-card)] border-r border-[color:var(--jarvis-border)] border-[color:var(--jarvis-border)] overflow-y-auto">
              <Content
                setTokenData={setTokenData}
                setSidebarOpen={setSidebarOpen}
                setShowTokenModal={setShowTokenModal}
              />
            </div>
          </Transition.Child>
        </Transition>
      )}

      {/* Token Modal */}
      <TokenModal tokenData={tokenData} showTokenModal={showTokenModal} setShowTokenModal={setShowTokenModal} />
    </>
  );
};

export default Sidebar;
