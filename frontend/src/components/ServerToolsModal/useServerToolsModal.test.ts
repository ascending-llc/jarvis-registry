// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import type { GetServerToolsResponse, Tool } from '@/services/server/type';

import { toServerTools, useServerToolsModal } from './useServerToolsModal';

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

const makeTool = (name: string, mcpToolName: string | null = name): Tool => ({
  ...(mcpToolName === null ? {} : { mcpToolName }),
  function: { name: `fn_${name}`, description: `${name} description` },
});

const makeResponse = (overrides: Partial<GetServerToolsResponse> = {}): GetServerToolsResponse => ({
  id: 'server-1',
  tools: ['search', 'fetch'],
  toolFunctions: { search: makeTool('search'), fetch: makeTool('fetch') },
  disabledTools: ['fetch'],
  ...overrides,
});

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

const deferred = <T>(): Deferred<T> => {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

interface HookProps {
  isOpen: boolean;
  serverId: string;
  canManageTools: boolean;
}

const renderToolsHook = (initialProps: Partial<HookProps> = {}) => {
  const onClose = vi.fn();
  const view = renderHook((props: HookProps) => useServerToolsModal({ ...props, onClose, serverName: 'Server' }), {
    initialProps: { isOpen: true, serverId: 'server-1', canManageTools: true, ...initialProps },
  });
  return { ...view, onClose };
};

const renderLoaded = async (initialProps: Partial<HookProps> = {}) => {
  const view = renderToolsHook(initialProps);
  await waitFor(() => expect(view.result.current.loaded).toBe(true));
  return view;
};

beforeEach(() => {
  mocks.getServerTools.mockResolvedValue(makeResponse());
  mocks.updateServerTools.mockResolvedValue(makeResponse());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('toServerTools', () => {
  test('keeps an explicit mcpToolName', () => {
    expect(toServerTools({ key: makeTool('x', 'actual-name') })[0].mcpToolName).toBe('actual-name');
  });

  test('falls back to the toolFunctions key when mcpToolName is missing or empty', () => {
    const tools = toServerTools({ first: makeTool('a', null), second: makeTool('b', '') });
    expect(tools.map(tool => tool.mcpToolName)).toEqual(['first', 'second']);
  });

  test('returns an empty list for missing toolFunctions', () => {
    expect(toServerTools(undefined)).toEqual([]);
  });
});

describe('useServerToolsModal', () => {
  test('loads tools and the disabled set when opened', async () => {
    const { result } = await renderLoaded();

    expect(mocks.getServerTools).toHaveBeenCalledWith('server-1');
    expect(result.current.tools.map(tool => tool.mcpToolName)).toEqual(['search', 'fetch']);
    expect([...result.current.disabledTools]).toEqual(['fetch']);
    expect(result.current.loading).toBe(false);
  });

  test('does not fetch while closed', () => {
    renderToolsHook({ isOpen: false });
    expect(mocks.getServerTools).not.toHaveBeenCalled();
  });

  test('discards a stale response when the server changes mid-request', async () => {
    const first = deferred<GetServerToolsResponse>();
    const second = deferred<GetServerToolsResponse>();
    mocks.getServerTools.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result, rerender } = renderToolsHook();

    rerender({ isOpen: true, serverId: 'server-2', canManageTools: true });
    await act(async () => {
      second.resolve(makeResponse({ toolFunctions: { newer: makeTool('newer') }, disabledTools: [] }));
    });
    await act(async () => {
      first.resolve(makeResponse({ toolFunctions: { stale: makeTool('stale') }, disabledTools: ['stale'] }));
    });

    expect(result.current.tools.map(tool => tool.mcpToolName)).toEqual(['newer']);
    expect(result.current.disabledTools.size).toBe(0);
  });

  test('discards a response that arrives after the modal closed', async () => {
    const pending = deferred<GetServerToolsResponse>();
    mocks.getServerTools.mockReturnValueOnce(pending.promise);
    const { result, rerender } = renderToolsHook();

    rerender({ isOpen: false, serverId: 'server-1', canManageTools: true });
    await act(async () => {
      pending.resolve(makeResponse());
    });

    expect(result.current.tools).toEqual([]);
    expect(result.current.loaded).toBe(false);
  });

  test('resets state on close', async () => {
    const { result, rerender } = await renderLoaded();

    rerender({ isOpen: false, serverId: 'server-1', canManageTools: true });

    expect(result.current.tools).toEqual([]);
    expect(result.current.disabledTools.size).toBe(0);
    expect(result.current.loaded).toBe(false);
    expect(result.current.loading).toBe(false);
  });

  test('shows an error toast and stays unloaded when fetching fails', async () => {
    mocks.getServerTools.mockRejectedValueOnce(new Error('boom'));
    const { result } = renderToolsHook();

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.loaded).toBe(false);
    expect(mocks.showToast).toHaveBeenCalledWith('Failed to fetch tools', 'error');
  });

  test('toggleTool flips a tool in the disabled set', async () => {
    const { result } = await renderLoaded();

    act(() => result.current.toggleTool('search'));
    expect(result.current.disabledTools.has('search')).toBe(true);
    act(() => result.current.toggleTool('fetch'));
    expect(result.current.disabledTools.has('fetch')).toBe(false);
  });

  test('toggleTool is ignored before tools have loaded', () => {
    mocks.getServerTools.mockReturnValueOnce(new Promise(() => {}));
    const { result } = renderToolsHook();

    act(() => result.current.toggleTool('search'));
    expect(result.current.disabledTools.size).toBe(0);
  });

  test('toggleTool is ignored without manage permission', async () => {
    const { result } = await renderLoaded({ canManageTools: false });

    act(() => result.current.toggleTool('search'));
    expect(result.current.disabledTools.has('search')).toBe(false);
  });

  test('toggleTool is ignored while saving', async () => {
    const save = deferred<GetServerToolsResponse>();
    mocks.updateServerTools.mockReturnValueOnce(save.promise);
    const { result } = await renderLoaded();
    act(() => result.current.toggleTool('fetch'));

    act(() => {
      void result.current.handleSave();
    });
    expect(result.current.saving).toBe(true);
    act(() => result.current.toggleTool('search'));
    expect(result.current.disabledTools.has('search')).toBe(false);

    await act(async () => save.resolve(makeResponse()));
  });

  test('handleSave sends exactly the unticked mcpToolNames, then toasts and stays open', async () => {
    mocks.updateServerTools.mockResolvedValueOnce(makeResponse({ disabledTools: ['fetch', 'search'] }));
    const { result, onClose } = await renderLoaded();
    act(() => result.current.toggleTool('search'));

    await act(async () => {
      await result.current.handleSave();
    });

    expect(mocks.updateServerTools).toHaveBeenCalledTimes(1);
    const [serverId, payload] = mocks.updateServerTools.mock.calls[0];
    expect(serverId).toBe('server-1');
    expect([...payload.disabledTools].sort()).toEqual(['fetch', 'search']);
    expect(mocks.showToast).toHaveBeenCalledWith('Tool settings updated successfully', 'success');
    expect(onClose).not.toHaveBeenCalled();
    expect(result.current.loaded).toBe(true);
    expect(result.current.saving).toBe(false);
  });

  test('handleSave keys the payload by the fallback id when mcpToolName is missing', async () => {
    mocks.getServerTools.mockResolvedValueOnce(
      makeResponse({ toolFunctions: { legacy_tool: makeTool('legacy', null) }, disabledTools: [] }),
    );
    const { result } = await renderLoaded();
    expect(result.current.tools[0].mcpToolName).toBe('legacy_tool');
    act(() => result.current.toggleTool('legacy_tool'));

    await act(async () => {
      await result.current.handleSave();
    });

    expect(mocks.updateServerTools).toHaveBeenCalledWith('server-1', { disabledTools: ['legacy_tool'] });
  });

  test('a failed save keeps the edited state open and shows the server error', async () => {
    mocks.updateServerTools.mockRejectedValueOnce({ detail: 'Unknown tool names: nope' });
    const { result, onClose } = await renderLoaded();
    act(() => result.current.toggleTool('search'));

    await act(async () => {
      await result.current.handleSave();
    });

    expect(mocks.showToast).toHaveBeenCalledWith('Unknown tool names: nope', 'error');
    expect(onClose).not.toHaveBeenCalled();
    expect(result.current.saving).toBe(false);
    expect(result.current.disabledTools.has('search')).toBe(true);
    expect(result.current.hasChanges).toBe(true);
    expect(result.current.loaded).toBe(true);
  });

  test('handleSave does nothing without manage permission', async () => {
    const { result } = await renderLoaded({ canManageTools: false });

    await act(async () => {
      await result.current.handleSave();
    });

    expect(mocks.updateServerTools).not.toHaveBeenCalled();
  });

  test('requestClose closes when idle but is ignored while saving', async () => {
    const save = deferred<GetServerToolsResponse>();
    mocks.updateServerTools.mockReturnValueOnce(save.promise);
    const { result, onClose } = await renderLoaded();
    act(() => result.current.toggleTool('search'));

    act(() => {
      void result.current.handleSave();
    });
    act(() => result.current.requestClose());
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => save.resolve(makeResponse()));
    expect(onClose).not.toHaveBeenCalled();

    act(() => result.current.requestClose());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('hasChanges tracks whether the ticks differ from the loaded state', async () => {
    const { result } = await renderLoaded();
    expect(result.current.hasChanges).toBe(false);

    act(() => result.current.toggleTool('search'));
    expect(result.current.hasChanges).toBe(true);

    act(() => result.current.toggleTool('search'));
    expect(result.current.hasChanges).toBe(false);
  });

  test('hasChanges is false when two edits swap which tool is disabled back and forth', async () => {
    const { result } = await renderLoaded();

    act(() => result.current.toggleTool('fetch'));
    act(() => result.current.toggleTool('search'));
    expect(result.current.hasChanges).toBe(true);

    act(() => result.current.toggleTool('search'));
    act(() => result.current.toggleTool('fetch'));
    expect(result.current.hasChanges).toBe(false);
  });

  test('handleSave does nothing when nothing changed', async () => {
    const { result } = await renderLoaded();

    await act(async () => {
      await result.current.handleSave();
    });

    expect(mocks.updateServerTools).not.toHaveBeenCalled();
  });

  test('a successful save applies the server response, not the sent payload', async () => {
    // The backend drops names that are not current tools, and may return a refreshed tool list.
    mocks.updateServerTools.mockResolvedValueOnce(
      makeResponse({
        toolFunctions: { search: makeTool('search'), fetch: makeTool('fetch'), write: makeTool('write') },
        disabledTools: ['search'],
      }),
    );
    const { result } = await renderLoaded();
    act(() => result.current.toggleTool('search'));

    await act(async () => {
      await result.current.handleSave();
    });

    expect(result.current.tools.map(tool => tool.mcpToolName)).toEqual(['search', 'fetch', 'write']);
    expect([...result.current.disabledTools]).toEqual(['search']);
    expect(result.current.hasChanges).toBe(false);
  });

  test('after a save, further edits are measured against the saved state', async () => {
    mocks.updateServerTools.mockResolvedValueOnce(makeResponse({ disabledTools: ['fetch', 'search'] }));
    const { result } = await renderLoaded();
    act(() => result.current.toggleTool('search'));
    await act(async () => {
      await result.current.handleSave();
    });

    act(() => result.current.toggleTool('search'));
    expect(result.current.hasChanges).toBe(true);

    await act(async () => {
      await result.current.handleSave();
    });
    expect(mocks.updateServerTools).toHaveBeenLastCalledWith('server-1', { disabledTools: ['fetch'] });
  });

  test('a save response that arrives after the modal closed is discarded', async () => {
    const save = deferred<GetServerToolsResponse>();
    mocks.updateServerTools.mockReturnValueOnce(save.promise);
    const { result, rerender } = await renderLoaded();
    act(() => result.current.toggleTool('search'));
    act(() => {
      void result.current.handleSave();
    });

    rerender({ isOpen: false, serverId: 'server-1', canManageTools: true });
    await act(async () => save.resolve(makeResponse({ disabledTools: ['fetch', 'search'] })));

    expect(result.current.tools).toEqual([]);
    expect(result.current.disabledTools.size).toBe(0);
    expect(result.current.saving).toBe(false);
    expect(mocks.showToast).toHaveBeenCalledWith('Tool settings updated successfully', 'success');
  });

  test('a save response that arrives after switching servers is discarded', async () => {
    const save = deferred<GetServerToolsResponse>();
    mocks.updateServerTools.mockReturnValueOnce(save.promise);
    const { result, rerender } = await renderLoaded();
    act(() => result.current.toggleTool('search'));
    act(() => {
      void result.current.handleSave();
    });

    mocks.getServerTools.mockResolvedValueOnce(
      makeResponse({ id: 'server-2', toolFunctions: { other: makeTool('other') }, disabledTools: [] }),
    );
    rerender({ isOpen: true, serverId: 'server-2', canManageTools: true });
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(async () => save.resolve(makeResponse({ disabledTools: ['fetch', 'search'] })));

    expect(result.current.tools.map(tool => tool.mcpToolName)).toEqual(['other']);
    expect(result.current.disabledTools.size).toBe(0);
    expect(result.current.hasChanges).toBe(false);
  });
});
