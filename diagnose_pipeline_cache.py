import json
import logging
import sqlite3
import tempfile

from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import requests

from backend import cloud_pipeline as pipeline
from backend.engine import paddle_resilient as resilient
from backend.engine.ocr_job_journal import (
    DEFAULT_PATH,
    OCRJobJournal,
)

SCOPE = (
    "84cc21e20b6ca202d92b0844429ed315625"
    "c334c2cba5e362e0e55eda4927ba7"
)

PDF = Path(
    r"C:\Users\LENOVO\Downloads\chamtayy.pdf"
)

MODEL = "PaddleOCR-VL-1.6"

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
)

if not PDF.is_file():
    raise SystemExit("PDF not found.")

owner = str(UUID(
    input("Original upload user UUID: ").strip()
))

attempts = []
saved = {}


def deny_http(self, method, *args, **kwargs):
    attempts.append(str(method))
    raise RuntimeError(
        "HTTP request blocked by cache-only diagnostic."
    )


def capture_bundle(
    exam,
    questions,
    contents,
    crop_images,
    **kwargs,
):
    saved["exam"] = exam
    saved["questions"] = questions
    saved["crop_images"] = crop_images

    return {
        "exam_id": exam["id"],
        "title": exam["name"],
        "question_count": len(questions),
    }


with tempfile.TemporaryDirectory(
    prefix="quizpdf-cache-test-"
) as directory:

    journal_copy = Path(directory) / "jobs.sqlite3"

    with closing(
        sqlite3.connect(
            DEFAULT_PATH.resolve().as_uri() + "?mode=ro",
            uri=True,
        )
    ) as source, closing(
        sqlite3.connect(journal_copy)
    ) as destination:
        source.backup(destination)

    class CacheOnlyJournal(OCRJobJournal):

        def __init__(self, scope, path=DEFAULT_PATH):

            print("COMPUTED SCOPE:", scope)
            print("SCOPE MATCH:", scope == SCOPE)

            if scope != SCOPE:
                raise RuntimeError(
                    "Upload scope mismatch. "
                    "Do not submit a new OCR job."
                )

            super().__init__(
                scope,
                journal_copy,
            )

    with (
        patch.object(
            resilient,
            "OCRJobJournal",
            CacheOnlyJournal,
        ),
        patch.object(
            requests.sessions.Session,
            "request",
            deny_http,
        ),
        patch.object(
            pipeline,
            "CloudOCRCheckpoint",
        ) as checkpoint_class,
        patch.object(
            pipeline,
            "CloudRepository",
        ) as repository_class,
    ):

        checkpoint = checkpoint_class.return_value
        checkpoint.load.return_value = None

        repository = (
            repository_class.return_value
            .__enter__.return_value
        )

        repository.save_exam_bundle.side_effect = (
            capture_bundle
        )

        result = pipeline.process_cloud_pdf(
            PDF.read_bytes(),
            PDF.name,
            MODEL,
            owner,
            "diagnostic-only",
            progress=lambda stage: print(
                "PROGRESS:", stage
            ),
        )

    print("\n" + "=" * 60)
    print("PIPELINE RESULT")
    print("=" * 60)

    print(json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    ))

    for question in saved.get("questions", []):

        number = question.get(
            "source_question_number",
            question.get("question_id"),
        )

        if number not in (2, 4, 7, 13, 14):
            continue

        print("\nQUESTION:", number)

        print(json.dumps(
            {
                "content": question.get(
                    "question_content"
                ),
                "options": question.get(
                    "mcp_options"
                ),
                "requires_source_image": question.get(
                    "requires_source_image"
                ),
                "warning": question.get(
                    "parser_warning"
                ),
                "asset": question.get("asset"),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ))

    print("\n" + "=" * 60)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 60)

    print(
        "QUESTIONS:",
        len(saved.get("questions", [])),
    )

    print(
        "CROP IMAGES:",
        len(saved.get("crop_images", {})),
    )

    print(
        "BLOCKED HTTP ATTEMPTS:",
        attempts,
    )

    print("Original OCR journal: read only")
    print("Cloud persistence: mocked")
    print("DIAGNOSTIC COMPLETED")