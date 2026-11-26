"""Offline regression fixture for chamtayy.pdf; never submit OCR jobs."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import requests
from dotenv import load_dotenv

from .engine import paddle_resilient as resilient
from .engine.ocr_job_journal import DEFAULT_PATH, OCRJobJournal
from .engine.paddle_client import extract_page_texts
from .engine.parser_pipeline import parse_pdf_questions
from .engine.registered_ocr_context import (
    capture_registered_pages, geometry_page_for, remember_completed_batch,
)
from .engine.ocr_text_reconciliation import reconcile_scan_choices
from .engine.ocr_blank_recovery import recover_scan_blanks
from .engine.ocr_inline_image_verification import recover_inline_scan_blanks

# Fixture metadata, never imported by production engine.
SCOPE = "84cc21e20b6ca202d92b0844429ed315625c334c2cba5e362e0e55eda4927ba7"

JOBS = {
    "PaddleOCR-VL-1.6": "119293255782260736",
    "PP-OCRv6": "119297800680390656",
}

WORDS = {
    2: ("destroyed", "performed", "wanted", "combined"),
    4: ("parks", "conditions", "systems", "letters"),
}

STEMS = {
    7: "A fire-fighter was injured severely while he ________ to put out a fire.",
    13: "Vietnamese Independence Day, which is celebrated annually ________ September 2nd, holds great significance for all Vietnamese people.",
    14: "I ________ my best friend again since I went abroad five years ago.",
}

LOW = "\u0332"


def question_key(q):
    return (
        q.get("page_number"),
        q.get("source_question_number", q.get("question_id")),
    )


def single_text(blocks):
    if (
        isinstance(blocks, list)
        and len(blocks) == 1
        and isinstance(blocks[0], dict)
        and blocks[0].get("type") == "text"
        and isinstance(blocks[0].get("value"), str)
    ):
        return blocks[0]["value"]

    return None


def stem_text(q):
    blocks = q.get("question_content")

    if not isinstance(blocks, list):
        return None

    context = [
        q[k]
        for k in ("section_instruction", "shared_passage")
        if q.get(k)
    ]

    if (
        len(blocks) == len(context) + 1
        and all(
            single_text([b]) == v
            for b, v in zip(blocks, context)
        )
    ):
        blocks = blocks[len(context):]

    return single_text(blocks)


def score(questions):
    errors = []

    keys = [
        question_key(q)
        for q in questions
    ]

    if keys != [(1, n) for n in range(1, 18)]:
        errors.append(
            "unexpected_question_identity_or_order"
        )

    for q in questions:
        options = q.get("mcp_options", [])

        if (
            q.get("type") != "mcp"
            or q.get("multiple_choice") is not False
            or [o.get("label") for o in options] != list("ABCD")
        ):
            errors.append(
                f"unexpected_structure:{question_key(q)}"
            )

        if (
            any(
                o.get("is_correct") is not False
                for o in options
            )
            or q.get("fill_in_answers")
            or q.get("match_pairs")
        ):
            errors.append(
                f"unexpected_answer_data:{question_key(q)}"
            )

    def unique(n):
        matches = [
            q
            for q in questions
            if question_key(q) == (1, n)
        ]

        return matches[0] if len(matches) == 1 else {}

    checks = []

    def check(name, expected, actual):
        checks.append({
            "check": name,
            "expected": expected,
            "actual": actual,
            "passed": actual == expected,
        })

    for n, words in WORDS.items():
        for label, word in zip("ABCD", words):
            matches = [
                o
                for o in unique(n).get("mcp_options", [])
                if o.get("label") == label
            ]

            value = (
                single_text(matches[0].get("content"))
                if len(matches) == 1
                else None
            )

            count = 2 if n == 2 else 1

            marked = (
                word[:-count]
                + "".join(c + LOW for c in word[-count:])
            )

            check(
                f"{n}:{label}:text",
                word,
                value.replace(LOW, "")
                if value is not None
                else None,
            )

            check(
                f"{n}:{label}:format",
                marked,
                value,
            )

    for n, expected in STEMS.items():
        check(
            f"{n}:stem",
            expected,
            stem_text(unique(n)),
        )

    return {
        "passed": sum(c["passed"] for c in checks),
        "total": len(checks),
        "structure_errors": errors,
        "checks": checks,
    }


def replay(pdf, owner, journal_path):
    pdf = Path(pdf).resolve()
    contents = pdf.read_bytes()

    journal_path = Path(journal_path).resolve()

    if not journal_path.is_file():
        raise RuntimeError(
            "Original OCR journal does not exist."
        )

    hits = []
    attempts = []
    stages = {}

    def blocked(*args, **kwargs):
        attempts.append("HTTP request")
        raise RuntimeError(
            "Network disabled for benchmark."
        )

    with tempfile.TemporaryDirectory(
        prefix="quizpdf-baseline-"
    ) as directory:
        source_pdf = Path(directory) / "source.pdf"
        source_pdf.write_bytes(contents)

        copied = Path(directory) / "jobs.sqlite3"

        with closing(
            sqlite3.connect(
                journal_path.as_uri() + "?mode=ro",
                uri=True,
            )
        ) as src:
            src.execute("PRAGMA query_only=ON")

            with closing(sqlite3.connect(copied)) as dst:
                src.backup(dst)

        def journal_factory(scope, *args, **kwargs):
            if scope != SCOPE:
                raise RuntimeError(
                    "Scope mismatch: verify original PDF, "
                    "owner and OCR configuration."
                )

            return OCRJobJournal(scope, copied)

        def cache_only(batch, *, model, journal=None, **kwargs):
            if (
                not isinstance(journal, OCRJobJournal)
                or journal.scope != SCOPE
                or journal.path.resolve() != copied.resolve()
                or batch.start_page != 0
                or batch.page_count != 1
                or model not in JOBS
            ):
                raise RuntimeError(
                    "Unexpected cache fixture identity."
                )

            key = journal.batch_key(
                batch.start_page,
                batch.page_count,
                model,
            )

            with journal._transaction() as conn:
                record = journal._read(conn, key)

            if (
                record is None
                or record.state != "completed"
                or record.job_id != JOBS[model]
            ):
                raise RuntimeError(
                    "Required completed OCR job unavailable."
                )

            result = journal.load_result(
                key,
                record.claim_id,
                record.job_id,
                page_count=1,
            )

            if result is None:
                raise RuntimeError(
                    "Required cache missing or invalid."
                )

            pages, info = result

            remember_completed_batch(
                journal,
                batch,
                model,
                record.claim_id,
                record.job_id,
                pages,
            )

            hits.append(model)

            return record.job_id, pages, info

        with (
            patch.object(
                resilient,
                "OCRJobJournal",
                journal_factory,
            ),
            patch.object(
                resilient,
                "_process_batch",
                cache_only,
            ),
            patch.object(
                requests.sessions.Session,
                "request",
                blocked,
            ),
            capture_registered_pages(),
        ):
            _, raw = resilient.recognize_pdf(
                str(source_pdf),
                "PaddleOCR-VL-1.6",
                user_id=owner,
                filename=pdf.name,
            )

            primary = raw["pages"]

            if (
                hits != list(JOBS)
                or len(primary) != 1
                or geometry_page_for(primary[0]) is primary[0]
                or attempts
            ):
                raise RuntimeError(
                    "Both cache hits and supplemental "
                    "linkage are required."
                )

            parsed = parse_pdf_questions(
                contents,
                extract_page_texts(raw),
                pdf.stem,
                embed_context=True,
            )

            questions = parsed["questions"]

            stages["S0"] = copy.deepcopy(questions)

            for name, recover in (
                ("S1", reconcile_scan_choices),
                ("S2", recover_scan_blanks),
                ("S3", recover_inline_scan_blanks),
            ):
                recover(
                    str(source_pdf),
                    primary,
                    questions,
                    geometry_page_for=geometry_page_for,
                )

                stages[name] = copy.deepcopy(questions)

            if attempts:
                raise RuntimeError(
                    "Unexpected HTTP attempt."
                )

    return {
        "version": 1,
        "scope": "cached_scan_recovery",
        "pdf_sha256": hashlib.sha256(contents).hexdigest(),
        "cache_hits": hits,
        "http_attempts": attempts,
        "stages": {
            name: {
                "score": score(qs),
                "questions": qs,
            }
            for name, qs in stages.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--pdf",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--journal",
        type=Path,
        default=DEFAULT_PATH,
    )

    parser.add_argument(
        "--out",
        type=Path,
        required=True,
    )

    args = parser.parse_args()

    load_dotenv(
        Path(__file__).resolve().parent / ".env"
    )

    if args.out.exists():
        parser.error(
            "Output already exists; choose a new path."
        )

    owner = input(
        "Original upload user UUID: "
    ).strip()

    report = replay(
        args.pdf,
        owner,
        args.journal,
    )

    with args.out.open("x", encoding="utf-8") as f:
        json.dump(
            report,
            f,
            ensure_ascii=False,
            indent=2,
        )

    for name, stage in report["stages"].items():
        result = stage["score"]

        print(
            name,
            result["passed"],
            "/",
            result["total"],
            "structure errors:",
            result["structure_errors"],
        )

    print("CACHE HITS:", report["cache_hits"])
    print("HTTP ATTEMPTS:", report["http_attempts"])
    print("REPORT:", args.out)


if __name__ == "__main__":
    main()
