"""Read-only proposals for narrowly supported inline OCR omissions."""
from __future__ import annotations

import re


HEADING = re.compile(
    r"^\s*(?:Question|Câu|Bài)\s*(\d+)\s*[.):]\s*(.*)$",
    re.I,
)

OPTION = re.compile(
    r"^\s*[A-H]\s*[.):]\s*\S"
)

MARKED_ORDINAL = re.compile(
    r"\$\s*\\underline\{\s*\\text\{([1-9]\d?(?:st|nd|rd|th))\}"
    r"\s*\}\s*\$"
)


def _compact(value):
    """Remove whitespace for cross-model alignment."""

    return "".join(value.split())


def _stem_evidence(root, number):
    """Extract a unique, high-confidence supplemental question stem."""

    if (
        not isinstance(root, dict)
        or type(number) is not int
        or number < 1
    ):
        return None

    texts = root.get("rec_texts")
    scores = root.get("rec_scores")

    if (
        not isinstance(texts, list)
        or not isinstance(scores, list)
        or not 0 < len(texts) == len(scores) <= 5000
    ):
        return None

    if not all(
        isinstance(t, str)
        for t in texts
    ):
        return None

    starts = [
        i
        for i, t in enumerate(texts)
        if (m := HEADING.match(t))
        and int(m[1]) == number
    ]

    if len(starts) != 1:
        return None

    i = starts[0]

    parts = [
        HEADING.match(texts[i])[2].strip()
    ]

    used_scores = [
        scores[i]
    ]

    j = i + 1

    while j < len(texts) and not OPTION.match(texts[j]):

        if (
            HEADING.match(texts[j])
            or len(parts) >= 2
        ):
            return None

        parts.append(
            texts[j].strip()
        )

        used_scores.append(
            scores[j]
        )

        j += 1

    if (
        j == len(texts)
        or not all(parts)
        or not all(
            type(s) in (int, float)
            and .95 <= s <= 1
            for s in used_scores
        )
    ):
        return None

    return (
        " ".join(parts),
        min(used_scores),
    )


def propose_inline_reconstruction(
    stem,
    root,
    number,
):
    """
    Propose missing text, not a blank position.

    This function never mutates inputs, changes answers,
    or calls an OCR provider.

    The proposal requires independent image verification
    before it can be applied to a parsed question.
    """

    if (
        not isinstance(stem, str)
        or len(stem) > 500
        or "\n" in stem
    ):
        return None

    matches = list(
        MARKED_ORDINAL.finditer(stem)
    )

    if len(matches) != 1:
        return None

    mark = matches[0]

    prefix = " ".join(
        stem[:mark.start()].split()
    )

    suffix = " ".join(
        stem[mark.end():].split()
    )

    ordinal = mark[1]

    if (
        not re.search(r"[a-z]$", prefix)
        or not re.match(r"^[,;:]", suffix)
        or any(
            c in prefix + suffix
            for c in "$\\{}_\u0332"
        )
        or min(
            len(prefix.split()),
            len(suffix.split()),
        ) < 4
    ):
        return None

    evidence = _stem_evidence(
        root,
        number,
    )

    if evidence is None:
        return None

    secondary, confidence = evidence

    compact = _compact(secondary)

    anchor = _compact(prefix)

    if not compact.startswith(anchor):
        return None

    tail = compact[len(anchor):]

    match = re.match(
        r"([A-Z][a-z]{2,24})"
        + re.escape(ordinal),
        tail,
    )

    if not match:
        return None

    inserted = match[1]

    actual_suffix = tail[match.end():]

    difference = None

    if actual_suffix != _compact(suffix):

        # Permit one narrow discrepancy:
        # the first suffix word may lose its final "s"
        # in supplemental OCR.
        #
        # Always preserve the primary OCR suffix.

        variant = re.match(
            r"^([,;:]\s*)([A-Za-z]{3,})s\b",
            suffix,
        )

        if variant is None:
            return None

        shortened = (
            variant[1]
            + variant[2]
            + suffix[variant.end():]
        )

        if actual_suffix != _compact(shortened):
            return None

        difference = {
            "primary": variant[2] + "s",
            "secondary": variant[2],
        }

    return {
        "prefix": prefix,
        "inserted_word": inserted,
        "ordinal": ordinal,
        "suffix": suffix,
        "text_without_blank": (
            prefix
            + " "
            + inserted
            + " "
            + ordinal
            + suffix
        ),
        "suffix_difference": difference,
        "confidence": confidence,
        "requires_image_verification": True,
    }