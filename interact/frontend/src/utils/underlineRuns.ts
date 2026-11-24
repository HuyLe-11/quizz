/** Convert combining underline marks to explicit renderable text runs. */
export interface UnderlineRun {
    text: string;
    underlined: boolean;
}

const COMBINING_LOW_LINE = "\u0332";

/**
 * PDF geometry yields e.g. `co\u0332a\u0332st` for coast (oa underlined).
 * The browser may position Unicode combining low lines incorrectly; convert
 * them to separately styled text runs without changing any actual letters.
 */
export function splitUnderlineRuns(value: string): UnderlineRun[] {
    const result: UnderlineRun[] = [];
    const characters = Array.from(value);
    let text = "";
    let underlined = false;

    const flush = () => {
        if (text) {
            result.push({ text, underlined });
            text = "";
        }
    };

    for (let i = 0; i < characters.length; i++) {
        const character = characters[i];

        if (character === COMBINING_LOW_LINE) {
            continue;
        }

        const nextIsUnderline = characters[i + 1] === COMBINING_LOW_LINE;

        if (nextIsUnderline !== underlined) {
            flush();
            underlined = nextIsUnderline;
        }

        text += character;

        if (nextIsUnderline) {
            i++;
        }
    }

    flush();
    return result;
}
