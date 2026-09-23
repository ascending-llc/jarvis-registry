// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import type { GetServerToolsResponse } from '@/services/server/type';

import ServerToolsModal from './index';

const mocks = vi.hoisted(() => ({
  getServerTools: vi.fn(),
  updateServerTools: vi.fn(),
  showToast: vi.fn(),
}));

vi.mock('@/services', () => ({
  default: { SERVER: { getServerTools: mocks.getServerTools, updateServerTools: mocks.updateServerTools } },
}));

vi.mock('@/contexts/GlobalContext', () => ({
  useGlobal: () => ({ showToast: mocks.showToast }),
}));

const makeResponse = (disabledTools: string[]): GetServerToolsResponse => ({
  id: 'server-1',
  tools: ['search', 'fetch'],
  toolFunctions: {
    search: { mcpToolName: 'search', function: { name: 'fn_search' } },
    fetch: { mcpToolName: 'fetch', function: { name: 'fn_fetch' } },
  },
  disabledTools,
});

const checkboxFor = (mcpToolName: string): HTMLInputElement =>
  document.getElementById(`tool-server-1-${mcpToolName}`) as HTMLInputElement;

const renderModal = async (onClose = vi.fn()) => {
  render(<ServerToolsModal isOpen onClose={onClose} serverId='server-1' serverName='GitHub' canManageTools />);
  await waitFor(() => expect(checkboxFor('search')).not.toBeNull());
  return { onClose };
};

// Headless UI's Dialog observes its panel size; jsdom has no ResizeObserver.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const closeButtons = (): HTMLElement[] => screen.queryAllByRole('button', { name: 'Close', hidden: true });

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub);
  mocks.getServerTools.mockResolvedValue(makeResponse(['fetch']));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe('ServerToolsModal footer', () => {
  test('with no edits, offers Close and disables Save', async () => {
    await renderModal();

    // The header X and the footer button are both labelled Close.
    expect(closeButtons()).toHaveLength(2);
    expect(screen.queryByRole('button', { name: 'Cancel', hidden: true })).toBeNull();
    expect((screen.getByRole('button', { name: 'Save Changes', hidden: true }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  test('an edit switches to Cancel and enables Save', async () => {
    await renderModal();

    fireEvent.click(checkboxFor('search'));

    expect(screen.getByRole('button', { name: 'Cancel', hidden: true })).toBeTruthy();
    expect(closeButtons()).toHaveLength(1);
    expect((screen.getByRole('button', { name: 'Save Changes', hidden: true }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  test('saving keeps the modal open and shows the saved state', async () => {
    mocks.updateServerTools.mockResolvedValueOnce(makeResponse(['fetch', 'search']));
    const { onClose } = await renderModal();
    fireEvent.click(checkboxFor('search'));

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save Changes', hidden: true }));
    });

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Cancel', hidden: true })).toBeNull());
    expect(onClose).not.toHaveBeenCalled();
    expect(closeButtons()).toHaveLength(2);
    expect(screen.getByText('Tools for GitHub')).toBeTruthy();
    expect(checkboxFor('search').checked).toBe(false);
    expect(checkboxFor('fetch').checked).toBe(false);
    expect((screen.getByRole('button', { name: 'Save Changes', hidden: true }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});
