import { createContext, useContext, useMemo } from 'react';
import ReactMarkdown, { type Components, type Options } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { SkillPreviewScanMarker, SkillPreviewScanText } from './SkillPreviewScanMarker';
import { resolveSkillFilePath } from './skillFileLinks';
import { getMarkdownNodeFindings } from './skillPreviewScanMarkdown';
import { groupScanFindingsByLocation, type LocatedScanFinding } from './skillScan';
import { getLanguageFromClassName, highlightCode } from './syntaxHighlighting';

type PreviewContext = {
  findings: ReadonlyMap<string, LocatedScanFinding>;
  lines: string[];
  bodyLineOffset: number;
  availableFilePaths: readonly string[];
  onSelectFile?: (path: string) => void;
  onShowFinding?: (key: string) => void;
};

const MarkdownPreviewContext = createContext<PreviewContext | null>(null);

const useMarkdownPreview = () => {
  const context = useContext(MarkdownPreviewContext);
  if (!context) throw new Error('Markdown preview context is unavailable.');
  return context;
};

// React keeps these renderers mounted when the scan status or parent rerenders.
const markdownComponents: Components = {
  div: ({ children, node }) => {
    const { findings, onShowFinding } = useMarkdownPreview();
    return (
      <SkillPreviewScanMarker findings={getMarkdownNodeFindings(node, findings)} onShowFinding={onShowFinding}>
        {children}
      </SkillPreviewScanMarker>
    );
  },
  h1: ({ children }) => <h1 className='mb-4 text-2xl font-bold text-[var(--jarvis-text-strong)]'>{children}</h1>,
  h2: ({ children }) => (
    <h2 className='mb-2.5 mt-5 text-base font-bold text-[var(--jarvis-text-strong)]'>{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className='mb-2 mt-4 text-[15px] font-bold text-[var(--jarvis-text-strong)]'>{children}</h3>
  ),
  p: ({ children }) => (
    <p className='mb-4 min-w-0 break-words text-[14.5px] leading-[1.75] text-[var(--jarvis-text)]'>{children}</p>
  ),
  ul: ({ children }) => <ul className='my-0 min-w-0 list-disc space-y-2 pl-5'>{children}</ul>,
  ol: ({ children }) => <ol className='my-0 min-w-0 list-decimal space-y-2 pl-5'>{children}</ol>,
  li: ({ children }) => (
    <li className='min-w-0 break-words text-sm leading-[1.6] text-[var(--jarvis-text)]'>{children}</li>
  ),
  strong: ({ children }) => <strong className='font-bold text-[var(--jarvis-text-strong)]'>{children}</strong>,
  pre: ({ children, node }) => {
    const { findings: bodyFindings, lines, bodyLineOffset, onShowFinding } = useMarkdownPreview();
    const findings = getMarkdownNodeFindings(node, bodyFindings);
    const position = node?.position?.start;
    const code = node?.children[0];
    const codeText = code?.type === 'element' ? code.children[0] : undefined;
    if (findings.length > 0 && position && code?.type === 'element' && codeText?.type === 'text') {
      const fence = /^(?:`{3,}|~{3,})/.test(lines[position.line - 1]?.slice(position.column - 1) ?? '');
      const text = codeText.value.replace(/\n$/, '');
      const firstLine = position.line + bodyLineOffset + (fence ? 1 : 0);
      const lastLine = firstLine + text.split('\n').length - 1;
      const groups = groupScanFindingsByLocation(findings);
      const inline = groups
        .filter(({ range: [start, end] }) => start <= lastLine && end >= firstLine)
        .flatMap(group => group.findings);
      const fenceFindings = groups
        .filter(({ range: [start, end] }) => start > lastLine || end < firstLine)
        .flatMap(group => group.findings);
      const className = code.properties.className;
      return (
        <SkillPreviewScanMarker findings={fenceFindings} onShowFinding={onShowFinding}>
          <div className='my-4 min-w-0 [overflow-wrap:normal]'>
            <SkillPreviewScanText
              text={text}
              firstLine={firstLine}
              findings={inline}
              language={getLanguageFromClassName(Array.isArray(className) ? className.join(' ') : undefined)}
              onShowFinding={onShowFinding}
              codeBlock
            />
          </div>
        </SkillPreviewScanMarker>
      );
    }
    return (
      <pre className='my-4 max-w-full overflow-auto whitespace-pre rounded-lg bg-[var(--jarvis-card-muted)] p-4 text-[12.5px] leading-[1.7] [overflow-wrap:normal] [&>code]:rounded-none [&>code]:bg-transparent [&>code]:p-0 [&>code]:whitespace-pre [&>code]:[overflow-wrap:normal]'>
        {children}
      </pre>
    );
  },
  code: ({ children, className }) => {
    const language = getLanguageFromClassName(className);
    if (language) {
      return (
        <code
          className={`skill-syntax-highlight ${className ?? ''} font-mono text-[12.5px]`}
          dangerouslySetInnerHTML={{ __html: highlightCode(String(children), language) }}
        />
      );
    }
    return (
      <code className='rounded bg-[var(--jarvis-card-muted)] px-1.5 py-0.5 font-mono text-[12.5px] [overflow-wrap:anywhere]'>
        {children}
      </code>
    );
  },
  a: ({ children, href }) => {
    const { availableFilePaths, onSelectFile } = useMarkdownPreview();
    const linkedSkillFilePath = onSelectFile ? resolveSkillFilePath(href, availableFilePaths) : null;
    return (
      <a
        href={href}
        target={linkedSkillFilePath ? undefined : '_blank'}
        rel={linkedSkillFilePath ? undefined : 'noreferrer'}
        onClick={event => {
          if (!linkedSkillFilePath) return;
          event.preventDefault();
          onSelectFile?.(linkedSkillFilePath);
        }}
        className='break-words text-[var(--jarvis-primary-text)] underline'
      >
        {children}
      </a>
    );
  },
  img: ({ src, alt, title }) => <img src={src} alt={alt ?? ''} title={title} className='h-auto max-w-full' />,
  table: ({ children }) => (
    <div className='mb-4 max-w-full overflow-auto rounded-lg border border-[color:var(--jarvis-border)]'>
      <table className='w-full min-w-max border-collapse text-left text-[13px]'>{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className='bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text-strong)]'>{children}</thead>
  ),
  tbody: ({ children }) => <tbody className='divide-y divide-[color:var(--jarvis-border)]'>{children}</tbody>,
  tr: ({ children }) => <tr>{children}</tr>,
  th: ({ children }) => <th className='min-w-0 break-words px-3 py-2 font-bold'>{children}</th>,
  td: ({ children }) => <td className='min-w-0 break-words px-3 py-2 text-[var(--jarvis-text)]'>{children}</td>,
};

type Props = Omit<PreviewContext, 'lines'> & {
  body: string;
  plugins: Options['rehypePlugins'];
};

const SkillMarkdownPreview = ({
  body,
  bodyLineOffset,
  plugins,
  findings,
  availableFilePaths,
  onSelectFile,
  onShowFinding,
}: Props) => {
  const lines = useMemo(() => body.split(/\r\n|\n/), [body]);
  const context = useMemo(
    () => ({ findings, lines, bodyLineOffset, availableFilePaths, onSelectFile, onShowFinding }),
    [findings, lines, bodyLineOffset, availableFilePaths, onSelectFile, onShowFinding],
  );
  return (
    <MarkdownPreviewContext.Provider value={context}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={plugins} components={markdownComponents}>
        {body}
      </ReactMarkdown>
    </MarkdownPreviewContext.Provider>
  );
};

export default SkillMarkdownPreview;
