// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import type { ModelGatewaySelection, ModelSourceListItem } from '@/services/model/type';
import type { WorkflowNode } from '../types';
import { ModelSourceSection } from './ModelSourceSection';
import { PropertiesContent } from './PropertiesContent';

interface ServerState {
  models: ModelSourceListItem[];
  modelLoading: boolean;
  modelError: string | null;
  modelSelection: ModelGatewaySelection;
  modelSelectionError: string | null;
  refreshModelData: () => Promise<void>;
}

const mocks = vi.hoisted(() => ({
  server: {} as ServerState,
  panel: {} as Record<string, unknown>,
}));

vi.mock('@/contexts/ServerContext', () => ({ useServer: () => mocks.server }));
vi.mock('./WorkflowPanelContext', () => ({ useWorkflowPanel: () => mocks.panel }));

// Sibling sections have their own concerns; stub them so only placement of the Model section is asserted.
vi.mock('./StepObjectiveEditor', () => ({ StepObjectiveEditor: () => <div data-testid='step-objective' /> }));
vi.mock('./UpstreamReferencesSection', () => ({
  UpstreamReferencesSection: () => <div data-testid='upstream-refs' />,
}));
vi.mock('./Nodes/PoolNodeProperties', () => ({ PoolNodeProperties: () => null }));
vi.mock('./Nodes/GateNodeProperties', () => ({ GateNodeProperties: () => null }));
vi.mock('./Nodes/ConditionNodeProperties', () => ({ ConditionNodeProperties: () => null }));
vi.mock('./Nodes/RouterNodeProperties', () => ({ RouterNodeProperties: () => null }));
vi.mock('./Nodes/LoopNodeProperties', () => ({ LoopNodeProperties: () => null }));
vi.mock('./Nodes/ParallelNodeProperties', () => ({ ParallelNodeProperties: () => null }));
vi.mock('./WorkflowProps', () => ({ default: () => null }));

const modelSource = (overrides: Partial<ModelSourceListItem>): ModelSourceListItem => ({
  id: 'id',
  displayName: 'Model',
  description: null,
  tags: [],
  mode: 'chat',
  providerType: 'anthropic',
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  ...overrides,
});

const SONNET = modelSource({ id: 'sonnet', displayName: 'Claude Sonnet', description: 'Fast and capable' });
const BEDROCK = modelSource({ id: 'bedrock', displayName: 'Bedrock Haiku', providerType: 'aws_bedrock' });
const EMBEDDING = modelSource({ id: 'embed', displayName: 'Titan Embed', mode: 'embedding' });

const DEFAULT_HELP = 'LLM that drives this step. Leave on workspace default unless the step needs a specific model.';

const setServer = (overrides: Partial<ServerState> = {}) => {
  mocks.server = {
    models: [SONNET, BEDROCK, EMBEDDING],
    modelLoading: false,
    modelError: null,
    modelSelection: { defaultWorkflowModelSourceId: 'sonnet', embeddingModelSourceId: 'embed' },
    modelSelectionError: null,
    refreshModelData: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
};

const renderSection = (modelSourceId: string | null, isReadOnly = false) => {
  const onChange = vi.fn();
  render(
    <MemoryRouter>
      <ModelSourceSection modelSourceId={modelSourceId} isReadOnly={isReadOnly} onChange={onChange} />
    </MemoryRouter>,
  );
  return { onChange, trigger: screen.getByRole('button', { name: /^Model/ }) };
};

const openOptions = (trigger: HTMLElement) => {
  fireEvent.click(trigger);
  return screen.getAllByRole('option');
};

beforeEach(() => setServer());

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ModelSourceSection states', () => {
  test('default with a known workspace default shows its name and the default help text', () => {
    const { trigger } = renderSection(null);

    expect(trigger.textContent).toContain('Workspace default · Claude Sonnet');
    expect(screen.getByText(DEFAULT_HELP)).toBeTruthy();
    // Only the "Workspace default" label is subtle (inherited from the trigger); the model name is not.
    expect(within(trigger).getByText('· Claude Sonnet').className).toContain('text-[var(--jarvis-text-strong)]');
  });

  test('default with no workspace default configured links to the Models page', () => {
    setServer({ modelSelection: { defaultWorkflowModelSourceId: null, embeddingModelSourceId: null } });
    const { trigger } = renderSection(null);

    expect(trigger.textContent).toContain('Workspace default · not configured');
    expect(screen.getByRole('link', { name: 'Models page' })).toBeTruthy();
    expect(openOptions(trigger)[0].textContent).toContain(
      'Not configured. MCP and A2A-pool steps using the workspace default require a default model. Select one on the Models page or choose a model for this step.',
    );
  });

  test('default with a failed selection request never says "not configured"', () => {
    setServer({
      modelSelection: { defaultWorkflowModelSourceId: null, embeddingModelSourceId: null },
      modelSelectionError: 'boom',
    });
    const { trigger } = renderSection(null);

    expect(trigger.textContent?.trim()).toBe('Workspace default');
    expect(screen.getByText(DEFAULT_HELP)).toBeTruthy();
    expect(openOptions(trigger)[0].textContent).toContain('Follows the workspace default.');
    expect(document.body.textContent).not.toContain('not configured');
  });

  test('default whose id is not in the loaded list is treated as unknown', () => {
    setServer({ modelSelection: { defaultWorkflowModelSourceId: 'missing', embeddingModelSourceId: null } });
    const { trigger } = renderSection(null);

    expect(trigger.textContent?.trim()).toBe('Workspace default');
    expect(document.body.textContent).not.toContain('not configured');
  });

  test('selected model shows its name and provider pill', () => {
    const { trigger } = renderSection('bedrock');

    expect(trigger.textContent).toContain('Bedrock Haiku');
    expect(trigger.textContent).toContain('AWS Bedrock');
    expect(screen.getByText(DEFAULT_HELP)).toBeTruthy();
  });

  test.each(['sonnet', null])('deleted model shows the warning state (default %s)', defaultId => {
    setServer({ modelSelection: { defaultWorkflowModelSourceId: defaultId, embeddingModelSourceId: null } });
    const { trigger } = renderSection('gone');

    expect(trigger.textContent).toContain('Unknown model (deleted)');
    expect(trigger.className).toContain('border-amber-600');
    expect(
      screen.getByText(
        'The selected model source is unavailable. Workflow runs cannot start with this configuration, and saving is blocked until you pick another model.',
        { exact: false },
      ),
    ).toBeTruthy();
  });

  test('a non-chat source id is treated as deleted', () => {
    const { trigger } = renderSection('embed');

    expect(trigger.textContent).toContain('Unknown model (deleted)');
  });

  test('loading disables the field and is checked before the deleted state', () => {
    setServer({ models: [], modelLoading: true });
    const { trigger } = renderSection('gone');

    expect((trigger as HTMLButtonElement).disabled).toBe(true);
    expect(trigger.textContent).toContain('Loading models…');
    expect(trigger.textContent).not.toContain('deleted');
  });

  test('load error with a selection keeps the raw id and offers Retry', () => {
    setServer({ models: [], modelError: 'boom' });
    const { trigger } = renderSection('model-123');

    expect((trigger as HTMLButtonElement).disabled).toBe(true);
    expect(trigger.textContent).toContain('Unavailable ·');
    expect(trigger.textContent).toContain('model-123');
    expect(screen.getByText(/Couldn't load models\. The current selection is kept\./)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(mocks.server.refreshModelData).toHaveBeenCalledTimes(1);
  });

  test('load error without a selection shows Workspace default', () => {
    setServer({ models: [], modelError: 'boom' });
    const { trigger } = renderSection(null);

    expect(trigger.textContent?.trim()).toBe('Workspace default');
    expect(screen.queryByText(/The current selection is kept/)).toBeNull();
  });

  test('view-only mode disables the field but shows the current value', () => {
    const { trigger } = renderSection('sonnet', true);

    expect((trigger as HTMLButtonElement).disabled).toBe(true);
    expect(trigger.className).toContain('disabled:opacity-60');
    expect(trigger.textContent).toContain('Claude Sonnet');
  });
});

describe('ModelSourceSection options', () => {
  test('lists Workspace default then only chat models, with provider and description', () => {
    const { trigger } = renderSection(null);
    const options = openOptions(trigger);

    expect(options.map(option => option.textContent)).toEqual([
      'Workspace defaultCurrently Claude Sonnet. Follows changes to the default.',
      'Claude SonnetAnthropic · Fast and capable',
      'Bedrock HaikuAWS Bedrock',
    ]);
  });

  test('choosing a model calls onChange with its id', () => {
    const { trigger, onChange } = renderSection(null);

    const bedrock = openOptions(trigger).find(option => within(option).queryByText('Bedrock Haiku'));
    if (!bedrock) throw new Error('Bedrock option not rendered');
    fireEvent.click(bedrock);

    expect(onChange).toHaveBeenCalledWith('bedrock');
  });

  test('choosing Workspace default calls onChange with null', () => {
    const { trigger, onChange } = renderSection('sonnet');

    fireEvent.click(openOptions(trigger)[0]);

    expect(onChange).toHaveBeenCalledWith(null);
  });
});

describe('PropertiesContent Model section placement', () => {
  const renderFor = (type: string) => {
    const node: WorkflowNode = {
      id: 'node-1',
      type,
      position: { x: 0, y: 0 },
      data: { label: 'Node', stepObjective: 'Do it', modelSourceId: 'sonnet' },
    };
    const onNodeDataChange = vi.fn();
    mocks.panel = {
      workflow: null,
      selectedNode: node,
      isReadOnly: false,
      onNodeDataChange,
      onSaveStepObjective: vi.fn(),
      onDeleteNode: vi.fn(),
    };
    render(
      <MemoryRouter>
        <PropertiesContent panelMode='node' isNewWorkflow={false} />
      </MemoryRouter>,
    );
    return { onNodeDataChange };
  };

  test.each(['mcp', 'pool'])('%s nodes show the section between step objective and upstream refs', type => {
    const { onNodeDataChange } = renderFor(type);

    const objective = screen.getByTestId('step-objective');
    const trigger = screen.getByRole('button', { name: /^Model/ });
    const refs = screen.getByTestId('upstream-refs');
    expect(objective.compareDocumentPosition(trigger) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(trigger.compareDocumentPosition(refs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(openOptions(trigger)[2]);
    expect(onNodeDataChange).toHaveBeenCalledWith('node-1', { modelSourceId: 'bedrock' });
  });

  test.each(['agent', 'gate', 'cond', 'router', 'loop', 'parallel'])('%s nodes do not show the section', type => {
    renderFor(type);

    expect(screen.queryByText('Model', { selector: 'label' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Model/ })).toBeNull();
  });
});
