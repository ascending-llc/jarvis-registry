// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test } from 'vitest';

import type { SkillOrigin } from '@/services/skill/type';

import SkillSourceChip, { getSkillSourceDisplay } from './SkillSourceChip';

afterEach(cleanup);

const externalProvider = (provider: SkillOrigin['provider']): SkillOrigin => ({
  kind: 'external_provider',
  provider,
});

describe('getSkillSourceDisplay', () => {
  test('registry origin uses the Jarvis icon and Registry label', () => {
    expect(getSkillSourceDisplay({ kind: 'registry' })).toEqual({
      label: 'Registry',
      tooltip: 'Created in Jarvis Registry',
      icon: 'jarvis',
    });
  });

  test('chat origin uses the Jarvis icon and Chat label', () => {
    expect(getSkillSourceDisplay({ kind: 'chat' })).toEqual({
      label: 'Chat',
      tooltip: 'Created in Jarvis Chat',
      icon: 'jarvis',
    });
  });

  test('github provider shows GitHub icon and name · repo@ref tooltip', () => {
    const display = getSkillSourceDisplay(
      externalProvider({ id: 's1', name: 'Acme', type: 'github', repo: 'acme/skills', ref: 'main' }),
    );
    expect(display).toEqual({ label: 'GitHub', tooltip: 'Acme · acme/skills@main', icon: 'github' });
  });

  test('non-github provider shows External label and external icon', () => {
    const display = getSkillSourceDisplay(
      externalProvider({ id: 's1', name: 'Acme', type: 'notion', repo: null, ref: null }),
    );
    expect(display).toEqual({ label: 'External', tooltip: 'Acme', icon: 'external' });
  });

  test('null name falls back to provider-unavailable tooltip', () => {
    const display = getSkillSourceDisplay(
      externalProvider({ id: 's1', name: null, type: 'github', repo: 'acme/skills', ref: 'main' }),
    );
    expect(display.tooltip).toBe('External provider unavailable');
  });

  test('null repo omits the repo segment', () => {
    const display = getSkillSourceDisplay(
      externalProvider({ id: 's1', name: 'Acme', type: 'github', repo: null, ref: 'main' }),
    );
    expect(display.tooltip).toBe('Acme');
  });

  test('null ref omits the @ref suffix', () => {
    const display = getSkillSourceDisplay(
      externalProvider({ id: 's1', name: 'Acme', type: 'github', repo: 'acme/skills', ref: null }),
    );
    expect(display.tooltip).toBe('Acme · acme/skills');
  });

  test('missing provider object never throws and falls back', () => {
    const display = getSkillSourceDisplay({ kind: 'external_provider' });
    expect(display).toEqual({ label: 'External', tooltip: 'External provider unavailable', icon: 'external' });
  });
});

describe('SkillSourceChip', () => {
  test('renders the favicon image for registry origins', () => {
    render(<SkillSourceChip origin={{ kind: 'registry' }} />);
    expect(screen.getByText('Registry')).toBeTruthy();
    expect(document.querySelector('img')).toBeTruthy();
  });

  test('renders the GitHub svg icon (not the favicon) for github providers', () => {
    render(
      <SkillSourceChip
        origin={externalProvider({ id: 's1', name: 'Acme', type: 'github', repo: 'acme/skills', ref: 'main' })}
      />,
    );
    expect(screen.getByText('GitHub')).toBeTruthy();
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('svg')).toBeTruthy();
  });
});
