import Prism from 'prismjs';

// Prism grammars register through side effects; keep dependency grammars before their consumers.
import 'prismjs/components/prism-markup';
import 'prismjs/components/prism-css';
import 'prismjs/components/prism-clike';
import 'prismjs/components/prism-c';
import 'prismjs/components/prism-cpp';
import 'prismjs/components/prism-csharp';
import 'prismjs/components/prism-go';
import 'prismjs/components/prism-java';
import 'prismjs/components/prism-kotlin';
import 'prismjs/components/prism-ruby';
import 'prismjs/components/prism-rust';
import 'prismjs/components/prism-sql';
import 'prismjs/components/prism-docker';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-jsx';
import 'prismjs/components/prism-tsx';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-markdown';

const FRONTMATTER_PATTERN = /^(---(?:\r?\n))([\s\S]*?)(\r?\n---(?=\r?\n|$))/;
const LANGUAGE_CLASS_PATTERN = /(?:^|\s)language-([^\s]+)/;

const HTML_ENTITIES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

const LANGUAGE_ALIASES: Record<string, string> = {
  'c#': 'csharp',
  'c++': 'cpp',
  cjs: 'javascript',
  cs: 'csharp',
  dockerfile: 'docker',
  dotnet: 'csharp',
  golang: 'go',
  html: 'markup',
  htm: 'markup',
  js: 'javascript',
  json5: 'json',
  kt: 'kotlin',
  kts: 'kotlin',
  md: 'markdown',
  mjs: 'javascript',
  mts: 'typescript',
  py: 'python',
  rb: 'ruby',
  rs: 'rust',
  shell: 'bash',
  sh: 'bash',
  svg: 'markup',
  ts: 'typescript',
  xml: 'markup',
  yml: 'yaml',
  zsh: 'bash',
};

const escapeHtml = (value: string): string => value.replace(/[&<>"']/g, character => HTML_ENTITIES[character]);

const resolveLanguage = (language?: string | null): string | null => {
  if (!language) return null;
  const normalized = language.trim().toLowerCase();
  return LANGUAGE_ALIASES[normalized] ?? normalized;
};

export const getLanguageFromClassName = (className?: string): string | null => {
  const match = className?.match(LANGUAGE_CLASS_PATTERN);
  return resolveLanguage(match?.[1]);
};

export const getLanguageFromPath = (path: string): string | null => {
  const filename = path.split('/').pop()?.toLowerCase();
  if (!filename) return null;

  const extensionSeparator = filename.lastIndexOf('.');
  const language = resolveLanguage(extensionSeparator > 0 ? filename.slice(extensionSeparator + 1) : filename);
  return language && Prism.languages[language] ? language : null;
};

export const highlightCode = (code: string, language?: string | null): string => {
  const resolvedLanguage = resolveLanguage(language);
  const grammar = resolvedLanguage ? Prism.languages[resolvedLanguage] : undefined;
  if (!resolvedLanguage || !grammar) return escapeHtml(code);

  try {
    return Prism.highlight(code, grammar, resolvedLanguage);
  } catch {
    return escapeHtml(code);
  }
};

export const highlightSkillMarkdown = (markdown: string): string => {
  const frontmatter = FRONTMATTER_PATTERN.exec(markdown);
  if (!frontmatter) return highlightCode(markdown, 'markdown');

  const bodyStart = frontmatter[0].length;
  return [
    escapeHtml(frontmatter[1]),
    highlightCode(frontmatter[2], 'yaml'),
    escapeHtml(frontmatter[3]),
    highlightCode(markdown.slice(bodyStart), 'markdown'),
  ].join('');
};
