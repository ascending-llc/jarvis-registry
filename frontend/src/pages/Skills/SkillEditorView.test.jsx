// @vitest-environment jsdom

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test, vi } from 'vitest';

import SkillEditorView from './SkillEditorView';

vi.mock('react-markdown', () => ({
  __esModule: true,
  default: ({ components }) => {
    const fixtureUrl = `https://example.com/${'unbroken-path/'.repeat(30)}`;
    const Paragraph = components.p ?? 'p';
    const Link = components.a ?? 'a';
    const Code = components.code ?? 'code';
    const Pre = components.pre ?? 'pre';
    const Image = components.img ?? 'img';
    return (
      <>
        <Paragraph>{`Paragraph ${fixtureUrl}`}</Paragraph>
        <Link href={fixtureUrl}>{fixtureUrl}</Link>
        <Code>{'inline-code-token-'.repeat(25)}</Code>
        <Pre>
          <Code>{'block-code-token-'.repeat(40)}</Code>
        </Pre>
        <Image src='https://example.com/wide.png' alt='wide fixture' />
      </>
    );
  },
}));

vi.mock('@/services', () => ({
  __esModule: true,
  default: {
    SKILL: {
      getSkillFile: vi.fn(),
    },
  },
}));

const LONG_DESCRIPTION = 'A'.repeat(1024);
const LONG_URL = `https://example.com/${'unbroken-path/'.repeat(30)}`;
const MARKDOWN_BODY = `# Overflow fixture

This paragraph contains ${LONG_URL} without a safe natural wrapping point.

[${LONG_URL}](${LONG_URL})

Inline code: \`${'inline-code-token-'.repeat(25)}\`

\`\`\`text
${'block-code-token-'.repeat(40)}
\`\`\`

![wide image](https://example.com/wide.png)`;

const createDraft = () => ({
  id: 'skill-overflow-fixture',
  stableName: 'overflow-fixture',
  markdown: {
    value: `---\nname: Overflow fixture\ndescription: ${LONG_DESCRIPTION}\n---\n\n${MARKDOWN_BODY}`,
    parsed: {
      displayTitle: 'Overflow fixture',
      description: LONG_DESCRIPTION,
      body: MARKDOWN_BODY,
      frontmatter: {
        name: 'Overflow fixture',
        description: LONG_DESCRIPTION,
      },
    },
    invalidInput: null,
  },
  category: 'Code',
  alwaysApply: true,
  enabled: true,
  version: 1,
  authorName: 'Test author',
  files: [
    {
      id: 'skill-md',
      relativePath: 'SKILL.md',
      mimeType: 'text/markdown',
      bytes: MARKDOWN_BODY.length,
      isExecutable: false,
    },
  ],
  permissions: {
    VIEW: true,
    EDIT: true,
    DELETE: true,
    SHARE: true,
  },
});

const renderView = ({ editorMode = 'preview', canEdit = true } = {}) => {
  const draft = createDraft();
  if (!canEdit) draft.permissions.EDIT = false;
  document.body.innerHTML = renderToStaticMarkup(
    <SkillEditorView
      draft={draft}
      loading={false}
      error={null}
      selectedPath='SKILL.md'
      editorMode={editorMode}
      saving={false}
      toggling={false}
      alwaysApply={draft.alwaysApply}
      onBack={vi.fn()}
      onRetry={vi.fn()}
      onSelectFile={vi.fn()}
      onEditorModeChange={vi.fn()}
      onNameChange={vi.fn()}
      onDescriptionChange={vi.fn()}
      onMarkdownChange={vi.fn()}
      onCategoryChange={vi.fn()}
      onAlwaysApplyChange={vi.fn()}
      onToggle={vi.fn()}
      onReset={vi.fn()}
      onSave={vi.fn()}
    />,
  );

  const section = document.body.querySelector('section');
  if (!(section instanceof HTMLElement)) throw new Error('Expected SkillEditorView section.');
  return section;
};

const expectClasses = (element, classes) => {
  expect(element).not.toBeNull();
  for (const className of classes) expect(element?.classList.contains(className)).toBe(true);
};

describe('SkillEditorView overflow containment', () => {
  test('keeps the flex layout and description inside the viewport', () => {
    const section = renderView();
    expectClasses(section, ['min-w-0', 'overflow-hidden']);

    const description = Array.from(section.querySelectorAll('span')).find(
      element => element.textContent === LONG_DESCRIPTION,
    );
    expectClasses(description ?? null, ['min-w-0', 'flex-1', 'truncate']);
    expectClasses(description?.parentElement ?? null, ['min-w-0', 'overflow-hidden']);

    const pathLabel = Array.from(section.querySelectorAll('span')).find(element => element.textContent === 'SKILL.md');
    expectClasses(pathLabel?.parentElement?.parentElement ?? null, ['min-w-0', 'overflow-hidden']);
  });

  test('wraps prose while keeping wide code blocks locally scrollable', () => {
    const section = renderView();
    const paragraph = Array.from(section.querySelectorAll('p')).find(element =>
      element.textContent?.includes(LONG_URL),
    );
    const link = section.querySelector(`a[href="${LONG_URL}"]`);
    const inlineCode = Array.from(section.querySelectorAll('code')).find(
      element => !element.parentElement?.matches('pre'),
    );
    const codeBlock = Array.from(section.querySelectorAll('pre')).find(element =>
      element.textContent?.includes('block-code-token'),
    );
    const image = section.querySelector('img');

    expectClasses(paragraph ?? null, ['break-words']);
    expectClasses(link, ['break-words']);
    expectClasses(inlineCode ?? null, ['[overflow-wrap:anywhere]']);
    expectClasses(codeBlock, ['max-w-full', 'overflow-auto']);
    expectClasses(image, ['h-auto', 'max-w-full']);
  });

  test('shows the full frontmatter block in preview mode', () => {
    const section = renderView();
    const frontmatter = Array.from(section.querySelectorAll('pre')).find(element =>
      element.textContent?.includes('name: Overflow fixture'),
    );

    expect(frontmatter?.textContent).toContain('description:');
    expect(frontmatter?.textContent).toMatch(/^---\n/);
    expect(frontmatter?.textContent).toMatch(/\n---$/);

    const readOnlyFrontmatter = Array.from(renderView({ canEdit: false }).querySelectorAll('pre')).find(element =>
      element.textContent?.includes('name: Overflow fixture'),
    );
    expect(readOnlyFrontmatter).toBeDefined();
  });

  test('shows Always Apply only to editors in edit mode', () => {
    const editableSection = renderView({ editorMode: 'edit' });
    const alwaysApply = editableSection.querySelector('[role="switch"][aria-label="Always apply"]');
    expect(alwaysApply?.getAttribute('aria-checked')).toBe('true');

    expect(renderView({ editorMode: 'preview' }).querySelector('[aria-label="Always apply"]')).toBeNull();
    expect(renderView({ editorMode: 'edit', canEdit: false }).querySelector('[aria-label="Always apply"]')).toBeNull();
  });
});
