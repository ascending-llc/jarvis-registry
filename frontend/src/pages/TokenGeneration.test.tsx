// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { AxiosError, AxiosHeaders, type AxiosResponse } from 'axios';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import type { GetTokenResponse } from '@/services/auth/type';

import TokenGeneration from './TokenGeneration';

const mocks = vi.hoisted(() => ({
  getToken: vi.fn(),
  user: {
    username: 'alice',
    scopes: ['servers-read'],
    tokenScopes: ['servers-read', 'mcp-proxy-ops', 'a2a-proxy-ops'],
  },
}));

vi.mock('@/services', () => ({
  default: { AUTH: { getToken: mocks.getToken } },
}));

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: mocks.user }),
}));

const successResponse = (scope: string): GetTokenResponse => ({
  success: true,
  tokenData: { accessToken: 'signed-token', expiresIn: 3600, tokenType: 'Bearer', scope },
  userScopes: mocks.user.tokenScopes,
  requestedScopes: mocks.user.tokenScopes,
});

const axiosErrorWithDetail = (status: number, detail: unknown): AxiosError => {
  const response = {
    status,
    statusText: '',
    data: { detail },
    headers: {},
    config: { headers: new AxiosHeaders() },
  } as AxiosResponse;
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', response.config, null, response);
};

const generateButton = (): HTMLButtonElement => screen.getByRole('button', { name: /Generate Token/ });

const submit = () => fireEvent.click(generateButton());

const selectCustomScopes = (value: string) => {
  fireEvent.click(screen.getByDisplayValue('custom'));
  fireEvent.change(screen.getByLabelText('Custom Scopes (JSON format)'), { target: { value } });
};

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe('TokenGeneration', () => {
  test('lists the token-eligible scopes from tokenScopes', () => {
    render(<TokenGeneration />);

    const card = screen.getByText('Scopes available for tokens:').parentElement as HTMLElement;
    for (const scope of mocks.user.tokenScopes) {
      expect(within(card).getByText(scope)).toBeTruthy();
    }
    expect(screen.queryByText('Current Scopes:')).toBeNull();
  });

  test('success card lists the scopes granted in the token and no description', async () => {
    mocks.getToken.mockResolvedValue(successResponse('mcp-proxy-ops a2a-proxy-ops'));
    render(<TokenGeneration />);

    fireEvent.change(screen.getByLabelText('Description (optional)'), { target: { value: 'my automation' } });
    submit();

    await screen.findByText('Token Generated Successfully');
    expect(screen.getByText('mcp-proxy-ops, a2a-proxy-ops')).toBeTruthy();
    expect(screen.queryByText(/Description:/)).toBeNull();
    expect(mocks.getToken).toHaveBeenCalledWith(expect.not.objectContaining({ requestedScopes: expect.anything() }));
  });

  test('a 400 shows a persistent alert and hides an earlier success card', async () => {
    mocks.getToken.mockResolvedValueOnce(successResponse('mcp-proxy-ops'));
    render(<TokenGeneration />);
    submit();
    await screen.findByText('Token Generated Successfully');

    mocks.getToken.mockRejectedValueOnce(axiosErrorWithDetail(400, 'None of your scopes can be granted'));
    submit();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('None of your scopes can be granted');
    expect(screen.queryByText('Token Generated Successfully')).toBeNull();

    // Stays until the next submit rather than clearing on a timer.
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  test('a list detail falls back to the generic message', async () => {
    mocks.getToken.mockRejectedValue(axiosErrorWithDetail(422, [{ loc: ['body'], msg: 'field required' }]));
    render(<TokenGeneration />);

    submit();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('Failed to generate token');
  });

  test.each([
    ['empty', ''],
    ['whitespace only', '   '],
    ['an empty array', '[]'],
  ])('custom scopes that are %s disable submit and send no request', async (_label, value) => {
    render(<TokenGeneration />);

    selectCustomScopes(value);

    expect(screen.getByText('Enter a JSON array with at least one scope')).toBeTruthy();
    expect(generateButton().disabled).toBe(true);
    fireEvent.submit(generateButton().closest('form') as HTMLFormElement);
    await waitFor(() => expect(mocks.getToken).not.toHaveBeenCalled());
  });

  test('non-empty custom scopes are sent as requestedScopes', async () => {
    mocks.getToken.mockResolvedValue(successResponse('mcp-proxy-ops'));
    render(<TokenGeneration />);

    selectCustomScopes('["mcp-proxy-ops"]');
    expect(generateButton().disabled).toBe(false);
    submit();

    await screen.findByText('Token Generated Successfully');
    expect(mocks.getToken).toHaveBeenCalledWith(expect.objectContaining({ requestedScopes: ['mcp-proxy-ops'] }));
  });
});
