// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import AuthenticationConfig from './AuthenticationConfig';
import type { AuthenticationConfig as AuthConfigType } from './types';

vi.mock('@/services', () => ({
  default: { MCP: { getDiscover: vi.fn() } },
}));

vi.mock('@/services/request', () => ({
  default: { cancels: {} },
}));

const BEARER_HINT = 'Sent as "Authorization: Bearer <key>".';
const BASIC_HINT = 'Sent as "Authorization: Basic <key>". The key is base64-encoded if it isn\'t already.';
const CUSTOM_HINT =
  'Sent as "<Custom Header Name>: <key>", exactly as entered. Enter the full header value, including any scheme prefix (e.g. "ApiKey abc123").';

// Mirrors the parent form: AuthenticationConfig is controlled, so the parent must feed changes back in.
const ControlledAuthConfig = ({
  initialConfig,
  onChange,
}: {
  initialConfig: AuthConfigType;
  onChange: (config: AuthConfigType) => void;
}) => {
  const [config, setConfig] = useState(initialConfig);
  return (
    <AuthenticationConfig
      config={config}
      onChange={next => {
        onChange(next);
        setConfig(next);
      }}
      isEditMode
    />
  );
};

const renderAuthConfig = (initialConfig: AuthConfigType) => {
  const onChange = vi.fn();
  render(<ControlledAuthConfig initialConfig={initialConfig} onChange={onChange} />);
  return onChange;
};

// The API Key InputField has no id, so its label isn't associated with the input.
const queryApiKeyInput = () => screen.queryByPlaceholderText('...');

const clickButton = (name: string) => fireEvent.click(screen.getByRole('button', { name }));

afterEach(() => {
  cleanup();
});

describe('AuthenticationConfig', () => {
  test('switching from OAuth to API Key defaults source to admin and header format to bearer', () => {
    const onChange = renderAuthConfig({ type: 'oauth', clientId: 'client-1' });

    clickButton('API Key');

    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: 'apiKey', source: 'admin', authorizationType: 'bearer' }),
    );
    expect(queryApiKeyInput()).not.toBeNull();
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText(BEARER_HINT)).toBeTruthy();
  });

  test('switching to API Key keeps an existing source and header format', () => {
    const onChange = renderAuthConfig({ type: 'oauth', source: 'user', authorizationType: 'custom' });

    clickButton('API Key');

    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: 'apiKey', source: 'user', authorizationType: 'custom' }),
    );
    expect(queryApiKeyInput()).toBeNull();
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true);
  });

  test('API Key hint follows the selected header format', () => {
    renderAuthConfig({ type: 'apiKey', source: 'admin', authorizationType: 'bearer' });

    expect(screen.getByText(BEARER_HINT)).toBeTruthy();

    clickButton('Basic');
    expect(screen.getByText(BASIC_HINT)).toBeTruthy();
    expect(screen.queryByText(BEARER_HINT)).toBeNull();

    clickButton('Custom');
    expect(screen.getByText(CUSTOM_HINT)).toBeTruthy();
    expect(screen.queryByText(BASIC_HINT)).toBeNull();
  });
});
