// @vitest-environment jsdom

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test, vi } from 'vitest';
import { SKILL_MARKDOWN_PATH } from './constants';
import SkillContentPanel from './SkillContentPanel';
import type { EditorMode } from './types';

const TABLE_BODY = `| Variable | Description |
|----------|-------------|
| \`$base_path\` | Required. The common local folder under which all three project clones live. |
| \`$branch\` | Optional. The branch to check out. |`;

const renderPanel = ({
  markdownBody,
  editorMode = 'preview',
  canEdit = true,
}: {
  markdownBody: string;
  editorMode?: EditorMode;
  canEdit?: boolean;
}) => {
  document.body.innerHTML = renderToStaticMarkup(
    <SkillContentPanel
      skillId='skill-1'
      selectedPath={SKILL_MARKDOWN_PATH}
      markdown={`---\nname: table-fixture\n---\n\n${markdownBody}`}
      markdownBody={markdownBody}
      frontmatterSource='name: table-fixture'
      markdownError={null}
      editorMode={editorMode}
      canEdit={canEdit}
      onMarkdownChange={vi.fn()}
      onEditorModeChange={vi.fn()}
    />,
  );
  return document.body;
};

describe('SkillContentPanel markdown preview', () => {
  test('renders a GFM pipe table as a real <table>, not a collapsed single line', () => {
    const container = renderPanel({ markdownBody: TABLE_BODY });

    const table = container.querySelector('table');
    expect(table).not.toBeNull();

    const rows = table?.querySelectorAll('tr') ?? [];
    expect(rows).toHaveLength(3);

    const headerCells = Array.from(rows[0]?.querySelectorAll('th') ?? []).map(cell => cell.textContent);
    expect(headerCells).toEqual(['Variable', 'Description']);

    const firstDataRowCells = Array.from(rows[1]?.querySelectorAll('td') ?? []).map(cell => cell.textContent);
    expect(firstDataRowCells).toEqual([
      '$base_path',
      'Required. The common local folder under which all three project clones live.',
    ]);

    expect(container.textContent).not.toContain('|----------|-------------|');
  });

  test('keeps ordinary paragraphs unaffected by GFM table parsing', () => {
    const container = renderPanel({ markdownBody: 'Just a plain paragraph, no pipes here.' });

    expect(container.querySelector('table')).toBeNull();
    expect(container.querySelector('p')?.textContent).toBe('Just a plain paragraph, no pipes here.');
  });
});
