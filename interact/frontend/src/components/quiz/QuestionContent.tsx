import React, { useMemo } from 'react';
import hljs from 'highlight.js/lib/core';
import javascript from 'highlight.js/lib/languages/javascript';
import python from 'highlight.js/lib/languages/python';
import xml from 'highlight.js/lib/languages/xml';
import css from 'highlight.js/lib/languages/css';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import 'highlight.js/styles/github-dark.css'; // or appropriate theme

import katex from 'katex';
import 'katex/dist/katex.min.css';

// Register common languages
hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('python', python);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('css', css);
hljs.registerLanguage('bash', bash);
hljs.registerLanguage('json', json);

interface QuestionContentProps {
  content: string;
  className?: string;
}

/**
 * Parses content containing text, markdown images, code blocks, and LaTeX.
 */
function parseContent(text: string): React.ReactNode[] {
  if (!text) return [];

  const parts: React.ReactNode[] = [];
  let currentText = text;
  let keyIdx = 0;

  // Simple regex for code blocks: ```lang\ncode\n```
  const codeBlockRegex = /```(\w*)\n([\s\S]*?)```/g;
  
  // Parse code blocks first
  let lastIdx = 0;
  let match;

  // We need to parse images and math inside the non-code text
  const parseInline = (str: string, baseKey: number) => {
    const inlineParts: React.ReactNode[] = [];
    
    // Regex for inline math: $math$, block math: $$math$$
    // Regex for markdown images: ![alt](url)
    const tokenRegex = /\$\$(.*?)\$\$|\$(.*?)\$|!\[(.*?)\]\((.*?)\)/g;
    
    let inlineLastIdx = 0;
    let inlineMatch;
    
    while ((inlineMatch = tokenRegex.exec(str)) !== null) {
      if (inlineMatch.index > inlineLastIdx) {
        const textPart = str.substring(inlineLastIdx, inlineMatch.index);
        if (textPart) {
          // split by newline to preserve line breaks
          const lines = textPart.split('\n');
          lines.forEach((line, i) => {
            if (line) inlineParts.push(<span key={`text-${baseKey}-${inlineLastIdx}-${i}`}>{line}</span>);
            if (i < lines.length - 1) inlineParts.push(<br key={`br-${baseKey}-${inlineLastIdx}-${i}`} />);
          });
        }
      }

      if (inlineMatch[1]) {
        // $$ block math $$
        try {
          const html = katex.renderToString(inlineMatch[1], { displayMode: true, throwOnError: false });
          inlineParts.push(<div key={`math-block-${baseKey}-${inlineMatch.index}`} dangerouslySetInnerHTML={{ __html: html }} className="my-2" />);
        } catch (e) {
          inlineParts.push(<div key={`math-err-${baseKey}-${inlineMatch.index}`}>{inlineMatch[1]}</div>);
        }
      } else if (inlineMatch[2]) {
        // $ inline math $
        try {
          const html = katex.renderToString(inlineMatch[2], { displayMode: false, throwOnError: false });
          inlineParts.push(<span key={`math-inline-${baseKey}-${inlineMatch.index}`} dangerouslySetInnerHTML={{ __html: html }} />);
        } catch (e) {
          inlineParts.push(<span key={`math-err-${baseKey}-${inlineMatch.index}`}>{inlineMatch[2]}</span>);
        }
      } else if (inlineMatch[4]) {
        // ![alt](url)
        inlineParts.push(
          <img 
            key={`img-${baseKey}-${inlineMatch.index}`} 
            src={inlineMatch[4]} 
            alt={inlineMatch[3] || "image"} 
            className="max-w-full h-auto rounded-md my-3 border border-[var(--color-border)]"
            loading="lazy"
          />
        );
      }

      inlineLastIdx = tokenRegex.lastIndex;
    }

    if (inlineLastIdx < str.length) {
      const textPart = str.substring(inlineLastIdx);
      const lines = textPart.split('\n');
      lines.forEach((line, i) => {
        if (line) inlineParts.push(<span key={`text-end-${baseKey}-${i}`}>{line}</span>);
        if (i < lines.length - 1) inlineParts.push(<br key={`br-end-${baseKey}-${i}`} />);
      });
    }

    return inlineParts;
  };

  while ((match = codeBlockRegex.exec(currentText)) !== null) {
    if (match.index > lastIdx) {
      parts.push(...parseInline(currentText.substring(lastIdx, match.index), keyIdx++));
    }

    const lang = match[1] || 'plaintext';
    const code = match[2];
    
    let highlighted = code;
    try {
      if (lang && hljs.getLanguage(lang)) {
        highlighted = hljs.highlight(code, { language: lang }).value;
      } else {
        highlighted = hljs.highlightAuto(code).value;
      }
    } catch (e) {
      // fallback
    }

    parts.push(
      <pre key={`code-${keyIdx++}`} className="my-3 relative group">
        {lang && <div className="absolute top-0 right-0 px-2 py-1 text-xs font-mono text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)] rounded-bl-md rounded-tr-md border-b border-l border-[var(--color-border)] opacity-50 group-hover:opacity-100 transition-opacity">{lang}</div>}
        <code dangerouslySetInnerHTML={{ __html: highlighted }} className={`hljs language-${lang} block overflow-x-auto p-4 rounded-md text-sm`} />
      </pre>
    );

    lastIdx = codeBlockRegex.lastIndex;
  }

  if (lastIdx < currentText.length) {
    parts.push(...parseInline(currentText.substring(lastIdx), keyIdx++));
  }

  return parts;
}

/**
 * Renders rich content for questions, options, and explanations.
 */
export function QuestionContent({ content, className = "" }: QuestionContentProps) {
  const renderedContent = useMemo(() => parseContent(content), [content]);

  return (
    <div className={`leading-relaxed ${className}`}>
      {renderedContent}
    </div>
  );
}
