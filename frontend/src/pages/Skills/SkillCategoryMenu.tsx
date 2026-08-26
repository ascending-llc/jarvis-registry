import { Listbox, Transition } from '@headlessui/react';
import { ChevronDownIcon } from '@heroicons/react/20/solid';
import {
  AcademicCapIcon,
  CommandLineIcon,
  CubeIcon,
  LightBulbIcon,
  PaperAirplaneIcon,
  PencilIcon,
  PresentationChartLineIcon,
  ShoppingBagIcon,
  TicketIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { Fragment } from 'react';

import { SKILL_CATEGORIES, type SkillCategory } from './skillDraft';

type SkillCategoryMenuProps = {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
};

const CATEGORY_ICONS: Record<SkillCategory, typeof LightBulbIcon> = {
  Ideas: LightBulbIcon,
  Travel: PaperAirplaneIcon,
  Learning: AcademicCapIcon,
  Writing: PencilIcon,
  Shopping: ShoppingBagIcon,
  Code: CommandLineIcon,
  'Misc.': CubeIcon,
  Roleplay: TicketIcon,
  Finance: PresentationChartLineIcon,
};

const SkillCategoryMenu: React.FC<SkillCategoryMenuProps> = ({ value, disabled, onChange }) => {
  const selected = SKILL_CATEGORIES.find(category => category.label === value);
  const SelectedIcon = selected ? CATEGORY_ICONS[selected.label] : CubeIcon;

  return (
    <Listbox value={value} onChange={onChange} disabled={disabled}>
      {({ open }) => (
        <div className='relative'>
          <Listbox.Button className='inline-flex items-center gap-2 rounded-lg bg-[var(--jarvis-card-muted)] px-3.5 py-[9px] text-[13.5px] font-semibold text-[var(--jarvis-text)] transition hover:text-[var(--jarvis-text-strong)] focus:outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] disabled:cursor-default disabled:opacity-70'>
            <SelectedIcon
              className='h-[18px] w-[18px] flex-shrink-0'
              style={{ color: selected?.color ?? 'var(--jarvis-faint)' }}
            />
            {value || 'Choose category'}
            {!disabled && (
              <ChevronDownIcon
                className={`h-3 w-3 text-[var(--jarvis-faint)] transition-transform ${open ? 'rotate-180' : ''}`}
              />
            )}
          </Listbox.Button>

          <Transition as={Fragment} leave='transition ease-in duration-100' leaveFrom='opacity-100' leaveTo='opacity-0'>
            <Listbox.Options className='absolute right-0 z-30 mt-2 max-h-[360px] w-[194px] overflow-auto rounded-[10px] border border-[color:var(--jarvis-border-strong)] bg-[var(--jarvis-card)] p-1.5 shadow-xl focus:outline-none'>
              {SKILL_CATEGORIES.map(category => {
                const CategoryIcon = CATEGORY_ICONS[category.label];

                return (
                  <Listbox.Option key={category.label} value={category.label} as={Fragment}>
                    {({ active, selected: optionSelected }) => (
                      <li
                        className={`flex cursor-pointer select-none items-center gap-3 rounded-[7px] px-2.5 py-2 text-left text-[13.5px] leading-4 text-[var(--jarvis-text)] transition ${
                          active || optionSelected ? 'bg-[var(--jarvis-card-muted)]' : ''
                        }`}
                      >
                        <CategoryIcon className='h-[18px] w-[18px] flex-shrink-0' style={{ color: category.color }} />
                        <span className={`flex-1 ${optionSelected ? 'font-semibold' : ''}`}>{category.label}</span>
                      </li>
                    )}
                  </Listbox.Option>
                );
              })}
            </Listbox.Options>
          </Transition>
        </div>
      )}
    </Listbox>
  );
};

export default SkillCategoryMenu;
