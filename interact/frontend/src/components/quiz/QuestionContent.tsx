import React, { useMemo } from "react";
import hljs from "highlight.js/lib/core";
import javascript from "highlight.js/lib/languages/javascript";
import python from "highlight.js/lib/languages/python";
import xml from "highlight.js/lib/languages/xml";
import css from "highlight.js/lib/languages/css";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import "highlight.js/styles/github-dark.css";
import katex from "katex";
import "katex/dist/katex.min.css";
import { splitUnderlineRuns } from "../../utils/underlineRuns";

hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("python", python);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("css", css);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("json", json);

interface QuestionContentProps {
  content: string;
  className?: string;
}

/**
 * Render confirmed PDF underlines as CSS decoration, NOT Unicode U+0332.
 * The latter can render over the wrong glyph (or appear to duplicate one).
 * All letters remain exactly as returned by the backend.
 */
function renderTextLine(text: string, key: string): React.ReactNode[] {
  return splitUnderlineRuns(text).map((run, index) =>
      run.underlined ? (
          <span
              key={`${key}-${index}`}
              style={{
                textDecorationLine: "underline",
                textDecorationThickness: "1px",
                textUnderlineOffset: "0.13em",
                textDecorationSkipInk: "none",
              }}
          >
        {run.text}
      </span>
      ) : (
          <React.Fragment key={`${key}-${index}`}>
            {run.text}
          </React.Fragment>
      ),
  );
}

function renderPlainText(text: string, key: string): React.ReactNode[] {
  const lines = text.split("\n");
  const result: React.ReactNode[] = [];

  lines.forEach((line, index) => {
    result.push(...renderTextLine(line, `${key}-line-${index}`));

    if (index !== lines.length - 1) {
      result.push(<br key={`${key}-break-${index}`} />);
    }
  });

  return result;
}

function parseInline(text: string, keyPrefix: string): React.ReactNode[] {
  const result: React.ReactNode[] = [];
  const tokens = /\$\$(.*?)\$\$|\$(.*?)\$|!\[(.*?)\]\((.*?)\)/g;

  let previous = 0;
  let match: RegExpExecArray | null;
  let index = 0;

  while ((match = tokens.exec(text)) !== null) {
    if (match.index > previous) {
      result.push(
          ...renderPlainText(
              text.slice(previous, match.index),
              `${keyPrefix}-${index}-plain`,
          ),
      );
    }

    if (match[1] !== undefined) {
      const html = katex.renderToString(match[1], {
        displayMode: true,
        throwOnError: false,
      });

      result.push(
          <div
              key={`${keyPrefix}-${index}-block-math`}
              className="my-2"
              dangerouslySetInnerHTML={{ __html: html }}
          />,
      );
    } else if (match[2] !== undefined) {
      const html = katex.renderToString(match[2], {
        displayMode: false,
        throwOnError: false,
      });

      result.push(
          <span
              key={`${keyPrefix}-${index}-inline-math`}
              dangerouslySetInnerHTML={{ __html: html }}
          />,
      );
    } else if (match[4] !== undefined) {
      result.push(
          <img
              key={`${keyPrefix}-${index}-image`}
              src={match[4]}
              alt={match[3] || "hình ảnh"}
              className="max-w-full h-auto rounded-md my-3 border border-[var(--color-border)]"
              loading="lazy"
          />,
      );
    }

    previous = tokens.lastIndex;
    index++;
  }

  if (previous < text.length) {
    result.push(
        ...renderPlainText(text.slice(previous), `${keyPrefix}-tail`),
    );
  }

  return result;
}

function parseContent(text: string): React.ReactNode[] {
  if (!text) return [];

  const result: React.ReactNode[] = [];
  const codeBlocks = /```(\w*)\n([\s\S]*?)```/g;

  let previous = 0;
  let match: RegExpExecArray | null;
  let index = 0;

  while ((match = codeBlocks.exec(text)) !== null) {
    if (match.index > previous) {
      result.push(
          ...parseInline(
              text.slice(previous, match.index),
              `before-code-${index}`,
          ),
      );
    }

    const language = match[1] || "plaintext";
    const source = match[2];

    // Escaped fallback prevents raw OCR/code from becoming executable HTML.
    let highlighted = source
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");

    try {
      highlighted = hljs.getLanguage(language)
          ? hljs.highlight(source, { language }).value
          : hljs.highlightAuto(source).value;
    } catch {
      // Keep escaped text as the safe fallback.
    }

    result.push(
        <pre key={`code-${index}`} className="my-3 relative group">
        <code
            className={`hljs language-${language} block overflow-x-auto p-4 rounded-md text-sm`}
            dangerouslySetInnerHTML={{ __html: highlighted }}
        />
      </pre>,
    );

    previous = codeBlocks.lastIndex;
    index++;
  }

  if (previous < text.length) {
    result.push(
        ...parseInline(text.slice(previous), `tail-${index}`),
    );
  }

  return result;
}

export function QuestionContent({
                                  content,
                                  className = "",
                                }: QuestionContentProps) {
  const renderedContent = useMemo(
      () => parseContent(content),
      [content],
  );

  return (
      <div className={`leading-relaxed ${className}`}>
        {renderedContent}
      </div>
  );
}
