"""PaddleOCR-only geometry experiment; no production parser changes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pymupdf

from .engine import paddle_resilient
from .engine.ocr_job_journal import DEFAULT_PATH, MAX_RESULT_BYTES
from .inspect_glyph_cache import geometry_inventory


MODEL = "PP-OCRv6"

PROBE_OWNER = "00000000-0000-4000-8000-000000000001"

# Experimental cloud parameter names,
# not a verified API capability.
OPTIONS = {
    "returnWordBox": True,
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _scalar(value):
    return value if type(value) in (bool, int) else None


def describe_page(page):
    root = _dict(
        page.get("pruned_result")
    )

    raw = _dict(
        _dict(page.get("raw")).get("prunedResult")
    )

    pre = _dict(
        root.get("doc_preprocessor_res")
    )

    settings = _dict(
        pre.get("model_settings")
    )

    model_settings = _dict(
        root.get("model_settings")
    )

    inventory = geometry_inventory(page)
    fields = inventory["fields"]

    return {
        "return_word_box": _scalar(
            root.get("return_word_box")
        ),
        "use_doc_unwarping": _scalar(
            settings.get("use_doc_unwarping")
        ),
        "use_doc_orientation_classify": _scalar(
            settings.get("use_doc_orientation_classify")
        ),
        "use_doc_preprocessor": _scalar(
            model_settings.get("use_doc_preprocessor")
        ),
        "angle": _scalar(
            pre.get("angle")
        ),
        "width": _scalar(
            root.get("width")
        ),
        "height": _scalar(
            root.get("height")
        ),
        "rec_text_count": (
            len(root["rec_texts"])
            if isinstance(root.get("rec_texts"), list)
            else None
        ),
        "raw_pruned_matches": bool(
            root and raw and root == raw
        ),
        "possible_fine_geometry_fields": [
            f for f in fields
            if re.search(
                r"word|char|glyph|token",
                f["path"],
                re.I,
            )
            and not f["path"].endswith(
                "return_word_box"
            )
        ],
        "inventory": inventory,
    }


def inspect_existing(journal, scope, job_id):
    path = Path(journal).resolve()

    if (
        not path.is_file()
        or not re.fullmatch(r"[0-9a-f]{64}", scope)
    ):
        raise ValueError(
            "Invalid journal or scope."
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
            WHERE j.scope=?
              AND j.job_id=?
              AND j.state='completed'
              AND typeof(r.payload)='blob'
              AND length(r.payload)<=?
            """,
            (
                scope,
                job_id,
                MAX_RESULT_BYTES,
            ),
        ).fetchall()

    if len(rows) != 1:
        raise RuntimeError(
            "Expected exactly one completed cache."
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
        or not data["pages"]
        or not all(
            isinstance(p, dict)
            for p in data["pages"]
        )
        or not isinstance(data.get("data_info"), dict)
    ):
        raise RuntimeError(
            "Invalid cache payload."
        )

    return {
        "mode": "existing_cache",
        "job_id": job_id,
        "pages": [
            describe_page(p)
            for p in data["pages"]
        ],
    }


def run_experiment(pdf, journal):
    """Explicit opt-in, one-page PP-OCR job with persistent isolated journal."""

    pdf = Path(pdf).resolve()
    journal = Path(journal).resolve()

    production = DEFAULT_PATH.resolve()

    if (
        journal == production
        or (
            journal.exists()
            and production.exists()
            and journal.samefile(production)
        )
    ):
        raise ValueError(
            "Use a separate experimental journal."
        )

    if (
        not pdf.is_file()
        or pdf.stat().st_size > 1_500_000
    ):
        raise ValueError(
            "Expected one PDF no larger than 1.5 MB."
        )

    content = pdf.read_bytes()

    with pymupdf.open(
        stream=content,
        filetype="pdf",
    ) as doc:

        if (
            not doc.is_pdf
            or doc.needs_pass
            or len(doc) != 1
            or doc[0].rotation != 0
        ):
            raise ValueError(
                "Expected one unencrypted, unrotated PDF page."
            )

    original_options = (
        paddle_resilient._optional_payload
    )

    def options(model):
        return (
            dict(OPTIONS)
            if model == MODEL
            else original_options(model)
        )

    # Standalone process only.
    # Restore both env and function even on failure.
    env = {
        "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL": "",
        "PADDLEOCR_MAX_PAGES_PER_JOB": "1",
        "PADDLEOCR_SUBMIT_MAX_ATTEMPTS": "1",
    }

    with tempfile.TemporaryDirectory(
        prefix="quizpdf-paddle-probe-"
    ) as directory:

        source = Path(directory) / "source.pdf"

        source.write_bytes(content)

        with (
            patch.dict(os.environ, env),
            patch.object(
                paddle_resilient,
                "_optional_payload",
                options,
            ),
        ):
            _, raw = paddle_resilient.recognize_pdf(
                str(source),
                MODEL,
                user_id=PROBE_OWNER,
                filename=pdf.name,
                journal_path=journal,
            )

    pages = raw.get("pages")

    if (
        not isinstance(pages, list)
        or len(pages) != 1
        or not isinstance(pages[0], dict)
    ):
        raise RuntimeError(
            "Unexpected result page count."
        )

    return {
        "mode": "experimental_paddle",
        "model": MODEL,
        "requested_options": dict(OPTIONS),
        "job_id": raw.get("job_id"),
        "pdf_sha256": hashlib.sha256(
            content
        ).hexdigest(),
        "rasterized_pages": raw.get(
            "rasterized_pages",
            [],
        ),
        "pages": [
            describe_page(p)
            for p in pages
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    subs = parser.add_subparsers(
        dest="mode",
        required=True,
    )

    cached = subs.add_parser(
        "cached"
    )

    cached.add_argument(
        "--journal",
        type=Path,
        default=DEFAULT_PATH,
    )

    cached.add_argument(
        "--scope",
        required=True,
    )

    cached.add_argument(
        "--job-id",
        required=True,
    )

    live = subs.add_parser(
        "experiment"
    )

    live.add_argument(
        "--pdf",
        type=Path,
        required=True,
    )

    live.add_argument(
        "--journal",
        type=Path,
        required=True,
    )

    live.add_argument(
        "--env-file",
        type=Path,
    )

    live.add_argument(
        "--execute",
        action="store_true",
    )

    args = parser.parse_args()

    if args.mode == "cached":

        report = inspect_existing(
            args.journal,
            args.scope,
            args.job_id,
        )

    else:

        if not args.execute:
            parser.error(
                "Experiment may submit a new PaddleOCR job; "
                "explicit --execute required."
            )

        if args.env_file is not None:

            from dotenv import load_dotenv

            if not args.env_file.is_file():
                parser.error(
                    "Environment file not found."
                )

            load_dotenv(
                args.env_file,
                override=False,
            )

        report = run_experiment(
            args.pdf,
            args.journal,
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
