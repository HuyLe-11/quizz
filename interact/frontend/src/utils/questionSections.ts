export interface QuestionSections {
    instruction: string | null;
    body: string;
}

const HEADING =
    /^\s*#{1,6}[ \t]+/;

const EN_START =
    /^(?:Mark\s+the\s+letter\b|Read\s+the\s+(?:following\s+)?(?:passage|text)\b|Choose\s+the\s+(?:best|correct)\b|Select\s+the\s+(?:best|correct)\b|Identify\s+the\b)/i;

const VI_START =
    /^(?:Phần\s+(?:[IVXLCDM]+|\d+)\s*[.:-]|Câu\s+trắc\s+nghiệm|Thí\s+sinh\s+(?:trả\s+lời|chọn|làm)|Trong\s+mỗi\s+ý|Chọn\s+(?:đáp\s+án|câu|phương\s+án)|Đọc\s+(?:đoạn|bài))/iu;

const VI_CONTINUATION =
    /^(?:Thí\s+sinh|Mỗi\s+câu(?:\s+hỏi)?|Trong\s+mỗi\s+ý|Ở\s+mỗi\s+câu|Đối\s+với\s+mỗi\s+câu)\s/iu;

const EN_END =
    /(?:\b(?:following\s+(?:questions|exchanges|sentences|items)|(?:numbered\s+)?blanks|questions|items)\s+(?:from\s+)?\d+\s*(?:to|[-–—])\s*\d+|\bfollowing\s+(?:questions|exchanges|sentences|items)|\bquestions\s+below|\b(?:correct|best)\s+answer)[.:]?$/i;

const VI_END =
    /(?:một\s+(?:phương\s+án|đáp\s+án)(?:\s+đúng)?(?:\s+duy\s+nhất)?|chọn\s+đúng\s+hoặc\s+sai|trả\s+lời\s+ngắn|trả\s+lời\s+từ\s+câu\s+\d+\s+đến\s+câu\s+\d+|nhiều\s+phương\s+án\s+lựa\s+chọn|trắc\s+nghiệm\s+đúng\s*[,/]?\s*sai)[.:]?$/iu;

const QUESTION_START =
    /^(?:Question|Câu)\s+\d+\b/i;

const SPEAKER =
    String.raw`[-–—][ \t]+[\p{Lu}][\p{L}\p{M}.'’]*(?:[ \t]+[\p{Lu}][\p{L}\p{M}.'’]*){0,3}[ \t]*:`;


function removeHeading(
    line: string,
): string {
    return line
        .replace(HEADING, "")
        .replace(
            /[ \t]+#{1,6}[ \t]*$/,
            "",
        );
}


function forDetection(
    text: string,
): string {
    return text
        .trim()
        .replace(/^(?:\*\*|__)/, "")
        .replace(/(?:\*\*|__)$/, "")
        .trim();
}


/**
 * Preserve dialogue formatting when the instruction
 * is rendered separately from the question body.
 */
function preserveDialogue(
    body: string,
    instruction: string,
): string {
    if (
        !/\b(?:exchanges|dialogues?|conversations?)\b/i.test(
            instruction,
        )

        || /```|\$|!\[|`/.test(body)
    ) {
        return body;
    }

    const markers = [
        ...body.matchAll(
            new RegExp(
                String.raw`(?:^|\s)${SPEAKER}`,
                "gu",
            ),
        ),
    ];

    if (markers.length < 2) {
        return body;
    }

    return body.replace(
        new RegExp(
            String.raw`(\S)[ \t]+(?=${SPEAKER})`,
            "gu",
        ),
        "$1\n",
    );
}


/**
 * Separate an identifiable leading exam instruction
 * from the actual question body.
 *
 * If the boundary cannot be determined reliably,
 * retain the original content without splitting.
 */
export function splitQuestionSections(
    content: string,
): QuestionSections {
    const unchanged = {
        instruction: null,
        body: content,
    };

    const lines = content
        .replace(/\r\n/g, "\n")
        .split("\n");

    const start = lines.findIndex(
        (line) => line.trim() !== "",
    );

    if (start < 0) {
        return unchanged;
    }

    const marked =
        HEADING.test(lines[start]);

    const first = marked
        ? removeHeading(lines[start])
        : lines[start];

    const initial =
        forDetection(first);

    const vietnamese =
        VI_START.test(initial);

    if (
        !vietnamese

        && !EN_START.test(initial)
    ) {
        return unchanged;
    }

    const instructionLines: string[] = [];

    for (
        let i = start;
        i < Math.min(
            lines.length - 1,
            start + 12,
        );
        i++
    ) {
        const line = i === start
            ? first
            : lines[i];

        if (
            !line.trim()

            || /^\s*```/.test(line)
        ) {
            break;
        }

        instructionLines.push(line);

        let next = i + 1;

        while (
            next < lines.length

            && !lines[next].trim()
            ) {
            next++;
        }

        if (next === lines.length) {
            break;
        }

        const candidate =
            forDetection(
                instructionLines.join(" "),
            );

        const nextLine =
            lines[next].trim();

        // Vietnamese section instructions may contain
        // several sentences and paragraph breaks.
        if (
            vietnamese

            && VI_CONTINUATION.test(nextLine)
        ) {
            instructionLines.push(
                ...lines.slice(i + 1, next),
            );

            i = next - 1;

            continue;
        }

        const knownEnding = (
            vietnamese
                ? VI_END
                : EN_END
        ).test(candidate);

        const explicitQuestion =
            QUESTION_START.test(nextLine);

        const singleSentence =
            i === start

            && /[.:]$/.test(candidate);

        const separatedHeading =
            marked

            && i === start

            && next > i + 1;

        if (
            knownEnding

            || explicitQuestion

            || singleSentence

            || separatedHeading
        ) {
            const instruction =
                instructionLines
                    .join("\n")
                    .trim();

            const body =
                lines.slice(next).join("\n");

            return {
                instruction,

                body: preserveDialogue(
                    body,
                    instruction,
                ),
            };
        }

        // Avoid consuming unrelated paragraphs
        // or source-code blocks.
        if (
            next > i + 1

            || /^```/.test(nextLine)

            || /[.!?:]$/.test(candidate)
        ) {
            break;
        }
    }

    return unchanged;
}