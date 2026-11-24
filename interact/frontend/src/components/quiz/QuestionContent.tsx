import { Fragment, useMemo, type ReactNode } from "react";
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

function renderPlainText(text: string, key: string): ReactNode[] {
    return text.split("\n").map((line, index) => (
        <Fragment key={`${key}-${index}`}>
            {index > 0 && <br />}
            {splitUnderlineRuns(line).map((run, i) => (
                <span key={i} style={run.underlined ? {
                    textDecorationLine: "underline",
                    textDecorationThickness: "1px",
                    textUnderlineOffset: "0.13em",
                    textDecorationSkipInk: "none",
                } : undefined}>{run.text}</span>
            ))}
        </Fragment>
    ));
}

function renderMath(
    source: string,
    displayMode: boolean,
    key: string,
): ReactNode {
    try {
        const html = katex.renderToString(source, {
            displayMode,
            throwOnError: false,
            trust: false,
        });

        return displayMode
            ? <div key={key} className="my-2" dangerouslySetInnerHTML={{ __html: html }} />
            : <span key={key} dangerouslySetInnerHTML={{ __html: html }} />;
    } catch {
        return <span key={key}>{source}</span>;
    }
}

function parseInline(text: string, key: string): ReactNode[] {
    const result: ReactNode[] = [];
    const tokens = /\$\$([\s\S]*?)\$\$|\$([^\n]*?)\$|!\[(.*?)\]\((.*?)\)/g;

    let previous = 0;

    for (const match of text.matchAll(tokens)) {
        const index = match.index;
        const id = `${key}-${index}`;

        result.push(
            ...renderPlainText(
                text.slice(previous, index),
                `${id}-text`,
            ),
        );

        if (match[1] !== undefined) {
            result.push(renderMath(match[1], true, id));
        } else if (match[2] !== undefined) {
            result.push(renderMath(match[2], false, id));
        } else if (match[4] !== undefined) {
            result.push(
                <img
                    key={id}
                    src={match[4]}
                    alt={match[3] || "Image"}
                    className="max-w-full h-auto rounded-md my-3 border border-[var(--color-border)]"
                    loading="lazy"
                />,
            );
        }

        previous = index + match[0].length;
    }

    result.push(
        ...renderPlainText(
            text.slice(previous),
            `${key}-tail`,
        ),
    );

    return result;
}

function renderCode(
    language: string,
    source: string,
    key: string,
): ReactNode {
    let highlighted: string | null = null;

    try {
        highlighted = hljs.getLanguage(language)
            ? hljs.highlight(source, { language }).value
            : hljs.highlightAuto(source).value;
    } catch {
        // React escapes the original source when highlighting fails.
    }

    const className =
        `hljs language-${language} block overflow-x-auto p-4 rounded-md text-sm`;

    return (
        <pre key={key} className="my-3 relative group">
      <span className="absolute top-0 right-0 px-2 py-1 text-xs font-mono text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)]">
        {language}
      </span>

            {highlighted === null
                ? <code className={className}>{source}</code>
                : <code
                    className={className}
                    dangerouslySetInnerHTML={{ __html: highlighted }}
                />}
    </pre>
    );
}

function parseContent(text: string): ReactNode[] {
    const result: ReactNode[] = [];
    const codeBlocks = /```([\w+-]*)\r?\n([\s\S]*?)```/g;

    let previous = 0;

    for (const match of text.matchAll(codeBlocks)) {
        const index = match.index;

        result.push(
            ...parseInline(
                text.slice(previous, index),
                `before-${index}`,
            ),
        );

        result.push(
            renderCode(
                match[1] || "plaintext",
                match[2],
                `code-${index}`,
            ),
        );

        previous = index + match[0].length;
    }

    result.push(
        ...parseInline(text.slice(previous), "tail"),
    );

    return result;
}

export function QuestionContent({
                                    content,
                                    className = "",
                                }: QuestionContentProps) {
    const rendered = useMemo(
        () => parseContent(content || ""),
        [content],
    );

    return (
        <div className={`leading-relaxed ${className}`}>
            {rendered}
        </div>
    );
}