"""Conservative recovery of damaged inline A-D option markers."""
from __future__ import annotations

import re


_EXPLICIT = re.compile(
    r"(?<!\S)([A-D])[.):,][ \t]*"
)

_PROTECTED = re.compile(
    r"\$\$.*?\$\$|\$[^$\n]*\$|"
    r"\\\(.*?\\\)|\\\[.*?\\\]|`[^`\n]*`"
)


def recover_four_options(
    line: str,
) -> tuple[str, list[dict[str, str]]] | None:
    """Require explicit A and D and at least three explicit markers.

    Recover at most one missing interior marker,
    only if unambiguous.

    Do not change any option content or interpret
    an answer as correct.
    """

    if "\n" in line or "\r" in line:
        return None

    protected = [
        match.span()
        for match in _PROTECTED.finditer(line)
    ]

    def outside(index: int) -> bool:
        return not any(
            start <= index < end
            for start, end in protected
        )

    markers = [
        match
        for match in _EXPLICIT.finditer(line)
        if outside(match.start())
    ]

    labels = [
        match.group(1)
        for match in markers
    ]

    if labels not in (
        list("ABCD"),
        list("ACD"),
        list("ABD"),
    ):
        return None

    if sum(
        line[match.start() + 1] == ","
        for match in markers
    ) > 1:
        return None

    if len(markers) == 3:

        if any(
            line[match.start() + 1] == ","
            for match in markers
        ):
            return None

        missing = (
            "B"
            if labels == list("ACD")
            else "C"
        )

        index = ord(missing) - ord("A")

        left = markers[index - 1].end()

        right = markers[index].start()

        bare = re.compile(
            r"(?<!\S)" + missing + r"[ \t]+"
        )

        candidates = [
            match
            for match in bare.finditer(
                line,
                left,
                right,
            )
            if outside(match.start())
        ]

        if len(candidates) != 1:
            return None

        markers.insert(
            index,
            candidates[0],
        )

    choices = []

    for index, marker in enumerate(markers):

        end = (
            markers[index + 1].start()
            if index < 3
            else len(line)
        )

        value = line[
            marker.end():end
        ].strip()

        if not value:
            return None

        choices.append({
            "label": "ABCD"[index],
            "text": value,
        })

    # A bare interior letter may belong to
    # the content instead of being an option marker.
    # Restrict this repair to pronunciation choices
    # with underline evidence in all four options.

    if len(labels) == 3 and not all(
        r"\underline" in choice["text"]
        for choice in choices
    ):
        return None

    return (
        line[:markers[0].start()].strip(),
        choices,
    )