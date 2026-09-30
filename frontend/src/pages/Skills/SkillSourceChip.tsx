import { GlobeAltIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { FaGithub } from 'react-icons/fa';

import faviconUrl from '@/assets/favicon-32x32.png';
import Tooltip from '@/components/Tooltip';
import type { SkillOrigin, SkillOriginProvider } from '@/services/skill/type';

import { SKILL_SOURCE_LABELS, SKILL_SOURCE_TOOLTIPS } from './constants';

type SkillSourceIcon = 'jarvis' | 'github' | 'external';

export type SkillSourceDisplay = {
  label: string;
  tooltip: string;
  icon: SkillSourceIcon;
};

const buildProviderTooltip = (provider: SkillOriginProvider | null | undefined): string => {
  if (!provider?.name) return SKILL_SOURCE_TOOLTIPS.providerUnavailable;
  if (!provider.repo) return provider.name;
  const repoRef = provider.ref ? `${provider.repo}@${provider.ref}` : provider.repo;
  return `${provider.name} · ${repoRef}`;
};

export const getSkillSourceDisplay = (origin: SkillOrigin): SkillSourceDisplay => {
  if (origin.kind === 'registry') {
    return { label: SKILL_SOURCE_LABELS.registry, tooltip: SKILL_SOURCE_TOOLTIPS.registry, icon: 'jarvis' };
  }
  if (origin.kind === 'chat') {
    return { label: SKILL_SOURCE_LABELS.chat, tooltip: SKILL_SOURCE_TOOLTIPS.chat, icon: 'jarvis' };
  }

  const provider = origin.provider;
  const tooltip = buildProviderTooltip(provider);
  if (provider?.type === 'github') {
    return { label: SKILL_SOURCE_LABELS.github, tooltip, icon: 'github' };
  }
  return { label: SKILL_SOURCE_LABELS.external, tooltip, icon: 'external' };
};

const SkillSourceChipIcon: React.FC<{ icon: SkillSourceIcon }> = ({ icon }) => {
  if (icon === 'jarvis') {
    return <img src={faviconUrl} width={14} height={14} alt='' className='h-3.5 w-3.5 flex-shrink-0' />;
  }
  if (icon === 'github') {
    return <FaGithub className='h-3.5 w-3.5 flex-shrink-0 text-[var(--jarvis-muted)]' aria-hidden='true' />;
  }
  return <GlobeAltIcon className='h-3.5 w-3.5 flex-shrink-0 text-[var(--jarvis-muted)]' aria-hidden='true' />;
};

const SkillSourceChip: React.FC<{ origin: SkillOrigin }> = ({ origin }) => {
  const { label, tooltip, icon } = getSkillSourceDisplay(origin);
  return (
    <Tooltip content={tooltip} placement='top' className='!w-auto'>
      <span className='inline-flex max-w-full items-center gap-1.5 rounded-full border border-[color:var(--jarvis-border)] bg-[var(--jarvis-surface)] px-2 py-[3px] text-[12px] font-medium leading-none text-[var(--jarvis-text)]'>
        <SkillSourceChipIcon icon={icon} />
        <span className='truncate'>{label}</span>
        {/* The hover tooltip is mouse-only; expose the same detail to the row button's accessible name. */}
        <span className='sr-only'>{tooltip}</span>
      </span>
    </Tooltip>
  );
};

export default SkillSourceChip;
