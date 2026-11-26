"""Inspect PaddleOCR word geometry in provider coordinates; no registration or OCR."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, asdict
from pathlib import Path

from .engine.ocr_job_journal import MAX_RESULT_BYTES


@dataclass(frozen=True, slots=True)
class WordSpan:
    start: int
    end: int
    text: str
    bbox: tuple[int, int, int, int]


def _box(value, size):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or not all(type(v) is int for v in value)
    ):
        raise ValueError("invalid_box")

    x0, y0, x1, y1 = value

    if not (
        0 <= x0 < x1
        and 0 <= y0 < y1
    ):
        raise ValueError("invalid_box")

    if size is not None and (
        x1 > size[0] or y1 > size[1]
    ):
        raise ValueError("box_outside_image")

    return tuple(value)


def align_words(
    text,
    line_box,
    words,
    boxes,
    *,
    size=None,
):
    """Preserve explicit whitespace tokens and allow omitted whitespace gaps."""

    if (
        not isinstance(text, str)
        or not text.strip()
    ):
        raise ValueError("invalid_line_text")

    if (
        not isinstance(words, list)
        or not isinstance(boxes, list)
        or not 0 < len(words) == len(boxes) <= 4096
    ):
        raise ValueError("invalid_word_arrays")

    line = _box(line_box, size)

    cursor = 0
    previous = None
    spans = []

    for word, raw_box in zip(words, boxes):

        if (
            not isinstance(word, str)
            or not word
        ):
            raise ValueError("invalid_word_text")

        # Prefer exact matching, including explicit whitespace.
        # Only skip omitted whitespace before a token that
        # does not begin with it.
        if (
            not text.startswith(word, cursor)
            and not word[0].isspace()
        ):
            while (
                cursor < len(text)
                and text[cursor].isspace()
            ):
                cursor += 1

        if not text.startswith(word, cursor):
            raise ValueError(
                "text_alignment_mismatch"
            )

        box = _box(raw_box, size)

        if not (
            line[0] <= box[0] < box[2] <= line[2]
            and line[1] <= box[1] < box[3] <= line[3]
        ):
            raise ValueError("word_outside_line")

        if (
            previous is not None
            and box[0] < previous[2]
        ):
            raise ValueError(
                "overlapping_or_reversed_words"
            )

        spans.append(
            WordSpan(
                cursor,
                cursor + len(word),
                word,
                box,
            )
        )

        cursor += len(word)
        previous = box

    if text[cursor:].strip():
        raise ValueError("uncovered_line_text")

    return tuple(spans)


def page_report(
    page,
    size=None,
    *,
    matches=(),
):
    root = page.get("pruned_result")
    raw = page.get("raw")

    if (
        not isinstance(root, dict)
        or not isinstance(raw, dict)
        or root != raw.get("prunedResult")
    ):
        raise ValueError(
            "pruned_result_mismatch"
        )

    settings = root.get("model_settings")

    if (
        not isinstance(settings, dict)
        or settings.get("use_doc_preprocessor") is not False
        or root.get("return_word_box") is not True
    ):
        raise ValueError(
            "unexpected_ocr_configuration"
        )

    arrays = [
        root.get(k)
        for k in (
            "rec_texts",
            "rec_boxes",
            "text_word",
            "text_word_boxes",
        )
    ]

    if (
        not all(
            isinstance(a, list)
            for a in arrays
        )
        or not 0 < len(arrays[0]) <= 5000
        or len({len(a) for a in arrays}) != 1
    ):
        raise ValueError(
            "page_array_count_mismatch"
        )

    counts = Counter()
    lengths = Counter()
    samples = []

    for i, (
        text,
        line_box,
        words,
        boxes,
    ) in enumerate(zip(*arrays)):

        if isinstance(words, list):
            lengths.update(
                len(w)
                for w in words
                if isinstance(w, str)
            )

        try:
            spans = align_words(
                text,
                line_box,
                words,
                boxes,
                size=size,
            )

            reason = "aligned_native_coordinates"

        except ValueError as exc:

            spans = ()
            reason = str(exc)

        counts[reason] += 1

        if (
            (not matches and i < 5)
            or (
                matches
                and isinstance(text, str)
                and any(m in text for m in matches)
            )
        ):
            samples.append({
                "line_index": i,
                "text": text,
                "line_box": line_box,
                "words": words,
                "word_boxes": boxes,
                "status": reason,
                "spans": [
                    asdict(s)
                    for s in spans
                ],
            })

    return {
        "coordinate_space": "paddle_provider_unregistered",
        "data_info_size": size,
        "line_count": len(arrays[0]),
        "line_status_counts": dict(counts),
        "token_codepoint_length_counts": dict(
            sorted(lengths.items())
        ),
        "samples": samples,
    }


def read_job(path, job_id):

    path = Path(path).resolve()

    if (
        not path.is_file()
        or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}",
            job_id,
        )
    ):
        raise ValueError(
            "Invalid journal or job ID."
        )

    with closing(
        sqlite3.connect(
            path.as_uri() + "?mode=ro",
            uri=True,
        )
    ) as conn:

        conn.execute(
            "PRAGMA query_only=ON"
        )

        rows = conn.execute(
            """
            SELECT r.payload, r.sha256
            FROM ocr_jobs j
            JOIN ocr_results r
              ON j.scope=r.scope
             AND j.batch_key=r.batch_key
             AND j.claim_id=r.claim_id
             AND j.job_id=r.job_id
            WHERE j.job_id=?
              AND j.state='completed'
              AND typeof(r.payload)='blob'
              AND length(r.payload)<=?
            LIMIT 2
            """,
            (
                job_id,
                MAX_RESULT_BYTES,
            ),
        ).fetchall()

    if len(rows) != 1:
        raise RuntimeError(
            "Expected one completed cache for the job ID."
        )

    payload, digest = rows[0]

    if (
        hashlib.sha256(payload).hexdigest()
        != digest
    ):
        raise RuntimeError(
            "Cache checksum mismatch."
        )

    data = json.loads(payload)

    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("pages"), list)
        or len(data["pages"]) != 1
        or not isinstance(data["pages"][0], dict)
        or not isinstance(data.get("data_info"), dict)
    ):
        raise ValueError(
            "Expected a one-page cache payload."
        )

    return data


def inspect_job(
    path,
    job_id,
    *,
    matches=(),
):
    data = read_job(
        path,
        job_id,
    )

    info_pages = data["data_info"].get("pages")

    size = None

    if (
        isinstance(info_pages, list)
        and len(info_pages) == 1
        and isinstance(info_pages[0], dict)
    ):
        w, h = (
            info_pages[0].get(k)
            for k in ("width", "height")
        )

        if (
            type(w) is int
            and type(h) is int
            and w > 0
            and h > 0
        ):
            size = (w, h)

    return {
        "job_id": job_id,
        **page_report(
            data["pages"][0],
            size,
            matches=matches,
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--journal",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--job-id",
        required=True,
    )

    parser.add_argument(
        "--match",
        action="append",
        default=[],
    )

    args = parser.parse_args()

    report = inspect_job(
        args.journal,
        args.job_id,
        matches=args.match,
    )

    print(
        json.dumps(
            report,
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
