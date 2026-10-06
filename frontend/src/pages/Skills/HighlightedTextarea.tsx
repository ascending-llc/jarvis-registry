import type React from 'react';
import { useLayoutEffect, useMemo, useRef } from 'react';

import { highlightSkillMarkdown } from './syntaxHighlighting';
import './syntax-highlighting.css';

type HighlightedTextareaProps = {
  value: string;
  ariaLabel: string;
  onChange: (value: string) => void;
};

const HighlightedTextarea: React.FC<HighlightedTextareaProps> = ({ value, ariaLabel, onChange }) => {
  const highlightRef = useRef<HTMLPreElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const highlightedMarkup = useMemo(() => {
    const highlighted = highlightSkillMarkdown(value);
    return value.endsWith('\n') ? `${highlighted} ` : highlighted;
  }, [value]);

  useLayoutEffect(() => {
    const highlight = highlightRef.current;
    const textarea = textareaRef.current;
    if (!highlight || !textarea) return;

    const syncSize = () => {
      highlight.style.width = `${textarea.offsetWidth}px`;
      highlight.style.height = `${textarea.offsetHeight}px`;
    };

    syncSize();
    const resizeObserver = new ResizeObserver(syncSize);
    resizeObserver.observe(textarea);
    return () => resizeObserver.disconnect();
  }, []);

  const handleScroll: React.UIEventHandler<HTMLTextAreaElement> = event => {
    if (!highlightRef.current) return;
    highlightRef.current.scrollTop = event.currentTarget.scrollTop;
    highlightRef.current.scrollLeft = event.currentTarget.scrollLeft;
  };

  return (
    <div className='skill-highlighted-textarea'>
      <pre
        ref={highlightRef}
        aria-hidden='true'
        className='skill-highlighted-textarea__highlight skill-syntax-highlight'
        dangerouslySetInnerHTML={{ __html: highlightedMarkup }}
      />
      <textarea
        ref={textareaRef}
        value={value}
        spellCheck={false}
        aria-label={ariaLabel}
        onChange={event => onChange(event.target.value)}
        onScroll={handleScroll}
        className='skill-highlighted-textarea__input'
      />
    </div>
  );
};

export default HighlightedTextarea;
