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

const INSTRUCTION_START =
    /^(?:\*\*|__)?\s*(?:Mark\s+the\s+letter\b|Read\s+the\s+(?:following\s+)?(?:passage|text)\b|Choose\s+the\s+(?:best|correct)\b|Select\s+the\s+(?:best|correct)\b|Identify\s+the\b|Chọn\s+(?:đáp\s+án|câu|phương\s+án)\b|Đọc\s+(?:đoạn|bài)\b)/i;

const MARKDOWN_HEADING =
    /^\s*#{1,6}[ \t]+/;

const INSTRUCTION_END =
    /(?:\b(?:following\s+(?:questions|exchanges|sentences|items)|(?:numbered\s+)?blanks|questions|items)\s+(?:from\s+)?\d+\s*(?:to|[-–—])\s*\d+|\bfollowing\s+(?:questions|exchanges|sentences|items)|\bquestions\s+below|\b(?:correct|best)\s+answer)\s*[.:]?\s*$/i;

const QUESTION_START =
    /^(?:Question|Câu)\s+\d+\b/i;

/**
 * Normalize leading section instructions for display.
 *
 * - Remove Markdown heading markers from recognized instructions.
 * - Support instructions without trailing punctuation.
 * - Support wrapped instructions and question-number ranges.
 * - Insert one blank line before question or passage content.
 * - Preserve existing paragraph breaks and stored question data.
 */
function separateInstruction(content: string): string {
    const lines = content
        .replace(/\r\n/g, "\n")
        .split("\n");

    const start = lines.findIndex(
        (line) => line.trim() !== "",
    );

    if (start === -1) {
        return content;
    }

    const first = lines[start].trim();

    const marked = MARKDOWN_HEADING.test(first);

    const heading = first.replace(
        MARKDOWN_HEADING,
        "",
    );

    // Do not interpret ordinary Markdown or source code
    // as an exam instruction.
    if (!INSTRUCTION_START.test(heading)) {
        return content;
    }

    if (marked) {
        lines[start] = heading.replace(
            /[ \t]+#{1,6}[ \t]*$/,
            "",
        );
    }

    const limit = Math.min(
        lines.length - 1,
        start + 8,
    );

    let boundary = -1;

    for (let i = start; i < limit; i++) {
        const line = lines[i].trim();

        if (
            !line
            || line.startsWith("```")
        ) {
            break;
        }

        if (
            i > start
            && QUESTION_START.test(line)
        ) {
            break;
        }

        // Join instruction lines only for boundary detection.
        // Do not modify their original layout.
        const ending = lines
            .slice(start, i + 1)
            .join(" ")
            .replace(/(?:\*\*|__)$/, "")
            .trimEnd();

        const nextLine = lines[i + 1].trim();

        const hasKnownEnding =
            INSTRUCTION_END.test(ending);

        const hasPunctuation =
            i === start
            && /[.:]$/.test(ending);

        const nextIsQuestion =
            QUESTION_START.test(nextLine);

        if (
            hasKnownEnding
            || hasPunctuation
            || nextIsQuestion
        ) {
            boundary = i;
            break;
        }

        // Do not scan across existing paragraphs
        // or unrelated complete sentences.
        if (
            !nextLine
            || /[.!?:]$/.test(ending)
        ) {
            break;
        }
    }

    // A recognized Markdown heading is normally one line.
    // Prefer a known wrapped boundary when available.
    if (
        boundary === -1
        && marked
    ) {
        boundary = start;
    }

    // Insert a single blank line only when necessary.
    if (
        boundary !== -1
        && lines[boundary + 1]?.trim()
    ) {
        lines.splice(
            boundary + 1,
            0,
            "",
        );
    }

    return lines.join("\n");
}

/**
 * Render confirmed PDF underlines using CSS decoration.
 */
function renderTextLine(
    text: string,
    key: string,
): React.ReactNode[] {
    return splitUnderlineRuns(text).map(
        (run, index) =>
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
                <React.Fragment
                    key={`${key}-${index}`}
                >
                    {run.text}
                </React.Fragment>
            ),
    );
}

function renderPlainText(
    text: string,
    key: string,
): React.ReactNode[] {
    const lines = text.split("\n");

    const result: React.ReactNode[] = [];

    lines.forEach((line, index) => {
        result.push(
            ...renderTextLine(
                line,
                `${key}-line-${index}`,
            ),
        );

        if (index !== lines.length - 1) {
            result.push(
                <br
                    key={`${key}-break-${index}`}
                />,
            );
        }
    });

    return result;
}

function parseInline(
    text: string,
    keyPrefix: string,
): React.ReactNode[] {
    const result: React.ReactNode[] = [];

    const tokens =
        /\$\$([\s\S]*?)\$\$|\$([^\n$]+?)\$|!\[(.*?)\]\((.*?)\)/g;

    let previous = 0;

    let match: RegExpExecArray | null;

    let index = 0;

    while (
        (match = tokens.exec(text)) !== null
        ) {
        if (match.index > previous) {
            result.push(
                ...renderPlainText(
                    text.slice(
                        previous,
                        match.index,
                    ),
                    `${keyPrefix}-${index}-plain`,
                ),
            );
        }

        if (match[1] !== undefined) {
            const html = katex.renderToString(
                match[1],
                {
                    displayMode: true,
                    throwOnError: false,
                    trust: false,
                },
            );

            result.push(
                <div
                    key={`${keyPrefix}-${index}-block-math`}
                    className="my-2"
                    dangerouslySetInnerHTML={{
                        __html: html,
                    }}
                />,
            );
        } else if (
            match[2] !== undefined
        ) {
            const html = katex.renderToString(
                match[2],
                {
                    displayMode: false,
                    throwOnError: false,
                    trust: false,
                },
            );

            result.push(
                <span
                    key={`${keyPrefix}-${index}-inline-math`}
                    dangerouslySetInnerHTML={{
                        __html: html,
                    }}
                />,
            );
        } else if (
            match[4] !== undefined
        ) {
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
            ...renderPlainText(
                text.slice(previous),
                `${keyPrefix}-tail`,
            ),
        );
    }

    return result;
}

function parseContent(
    text: string,
): React.ReactNode[] {
    if (!text) {
        return [];
    }

    const result: React.ReactNode[] = [];

    const codeBlocks =
        /```(\w*)\n([\s\S]*?)```/g;

    let previous = 0;

    let match: RegExpExecArray | null;

    let index = 0;

    while (
        (match = codeBlocks.exec(text)) !== null
        ) {
        if (match.index > previous) {
            result.push(
                ...parseInline(
                    text.slice(
                        previous,
                        match.index,
                    ),
                    `before-code-${index}`,
                ),
            );
        }

        const language =
            match[1] || "plaintext";

        const source = match[2];

        let highlighted = source
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");

        try {
            highlighted =
                hljs.getLanguage(language)
                    ? hljs.highlight(
                        source,
                        { language },
                    ).value
                    : hljs.highlightAuto(
                        source,
                    ).value;
        } catch {
            // Keep escaped text when highlighting fails.
        }

        result.push(
            <pre
                key={`code-${index}`}
                className="my-3 relative group"
            >
                <code
                    className={`hljs language-${language} block overflow-x-auto p-4 rounded-md text-sm`}
                    dangerouslySetInnerHTML={{
                        __html: highlighted,
                    }}
                />
            </pre>,
        );

        previous = codeBlocks.lastIndex;

        index++;
    }

    if (previous < text.length) {
        result.push(
            ...parseInline(
                text.slice(previous),
                `tail-${index}`,
            ),
        );
    }

    return result;
}

export function QuestionContent({
                                    content,
                                    className = "",
                                }: QuestionContentProps) {
    const renderedContent = useMemo(
        () => parseContent(
            separateInstruction(content),
        ),
        [content],
    );

    return (
        <div
            className={`leading-relaxed ${className}`}
        >
            {renderedContent}
        </div>
    );
}