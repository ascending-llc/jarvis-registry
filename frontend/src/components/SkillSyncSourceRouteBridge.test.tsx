// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, test, vi } from 'vitest';

import SkillSyncSourceRouteBridge from './SkillSyncSourceRouteBridge';

vi.mock('@/config', () => ({ getBasePathForUrl: () => '' }));

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid='location'>{`${location.pathname}${location.search}`}</div>;
};

const renderAt = (entry: string) =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path='/skill-sync-sources' element={<SkillSyncSourceRouteBridge list />} />
        <Route path='/skill-sync-sources/:id' element={<SkillSyncSourceRouteBridge />} />
        <Route path='*' element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );

const currentLocation = () => new URL(screen.getByTestId('location').textContent ?? '', 'http://jarvis.test');

afterEach(() => {
  cleanup();
});

describe('SkillSyncSourceRouteBridge', () => {
  test('the list route opens the external providers tab and keeps query params', () => {
    renderAt('/skill-sync-sources?error=invalid_callback');

    const location = currentLocation();
    expect(location.pathname).toBe('/');
    expect(location.searchParams.get('tab')).toBe('external');
    expect(location.searchParams.get('error')).toBe('invalid_callback');
  });

  test('the detail route opens the GitHub provider page and keeps the OAuth status', () => {
    renderAt('/skill-sync-sources/source-1?status=connected');

    const location = currentLocation();
    expect(location.pathname).toBe('/federation-edit');
    expect(location.searchParams.get('id')).toBe('source-1');
    expect(location.searchParams.get('provider')).toBe('github');
    expect(location.searchParams.get('status')).toBe('connected');
  });

  test('the detail route keeps an OAuth error', () => {
    renderAt('/skill-sync-sources/source-1?error=auth_failed');

    expect(currentLocation().searchParams.get('error')).toBe('auth_failed');
  });
});
