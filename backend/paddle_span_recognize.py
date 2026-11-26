"""Opt-in PP-OCRv6 atlas recognition; no production recovery."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pymupdf

from .engine import paddle_resilient
from .engine.ocr_job_journal import DEFAULT_PATH
from .paddle_geometry_probe import MODEL, OPTIONS

OWNER = "00000000-0000-4000-8000-000000000002"
MAX_BYTES = 1_500_000


def sha(data):
    return hashlib.sha256(data).hexdigest()


def preflight(folder):
    folder = Path(folder)

    manifest_bytes = (
        folder / "manifest.json"
    ).read_bytes()

    manifest = json.loads(manifest_bytes)

    content = (
        folder / "atlas.pdf"
    ).read_bytes()

    if (
        manifest.get("scope") != "diagnostic_only"
        or not 0 < len(content) <= MAX_BYTES
        or manifest.get("atlas_pdf_sha256") != sha(content)
        or manifest.get("atlas_pdf_bytes") != len(content)
    ):
        raise ValueError(
            "Atlas PDF identity or size mismatch"
        )

    pages = manifest["atlas_pages"]
    entries = manifest["candidates"]

    if (
        not 1 <= len(pages) <= 2
        or not 1 <= len(entries) <= 42
    ):
        raise ValueError(
            "Atlas budget exceeded"
        )

    with pymupdf.open(
        stream=content,
        filetype="pdf",
    ) as doc:

        if (
            doc.needs_pass
            or len(doc) != len(pages)
        ):
            raise ValueError(
                "Atlas page count mismatch"
            )

        for i, (page, meta) in enumerate(
            zip(doc, pages),
            1,
        ):
            w, h = meta["size"]

            if (
                meta["page"] != i
                or type(w) is not int
                or type(h) is not int
                or min(w, h) <= 0
                or w * h > 4_000_000
                or page.rotation
                or page.get_text().strip()
                or page.get_drawings()
                or len(page.get_images()) != 1
                or abs(page.rect.width * 2 - w) > .01
                or abs(page.rect.height * 2 - h) > .01
            ):
                raise ValueError(
                    "Invalid atlas page"
                )

            png = (
                folder / f"atlas-{i}.png"
            ).read_bytes()

            if sha(png) != meta["png_sha256"]:
                raise ValueError(
                    "Atlas PNG checksum mismatch"
                )

    return content, {
        "atlas_sha256": sha(content),
        "manifest_sha256": sha(manifest_bytes),
        "atlas_bytes": len(content),
        "atlas_sizes": [
            p["size"]
            for p in pages
        ],
        "candidate_count": len(entries),
        "page_count": len(pages),
    }


def summarize(raw, count):
    pages = raw.get("pages")

    if (
        not isinstance(pages, list)
        or len(pages) != count
        or raw.get("rasterized_pages")
        or raw.get("batches")
        or raw.get("job_ids")
        or not isinstance(raw.get("job_id"), str)
    ):
        raise ValueError(
            "Unexpected atlas OCR batch result"
        )

    info = raw.get("data_info") or {}

    sizes = info.get("pages")

    results = []

    for i, page in enumerate(pages):

        root = page.get("pruned_result")

        if (
            not isinstance(root, dict)
            or root != (
                page.get("raw") or {}
            ).get("prunedResult")
            or (
                root.get("model_settings") or {}
            ).get("use_doc_preprocessor") is not False
            or root.get("return_word_box") is not True
        ):
            raise ValueError(
                "Unexpected PaddleOCR result configuration"
            )

        texts, boxes, scores = (
            root.get(k)
            for k in (
                "rec_texts",
                "rec_boxes",
                "rec_scores",
            )
        )

        if (
            not all(
                isinstance(a, list)
                for a in (texts, boxes, scores)
            )
            or not len(texts) == len(boxes) == len(scores)
        ):
            raise ValueError(
                "Invalid OCR line arrays"
            )

        size = None

        if (
            isinstance(sizes, list)
            and len(sizes) == count
            and isinstance(sizes[i], dict)
        ):
            w, h = (
                sizes[i].get(k)
                for k in ("width", "height")
            )

            if (
                type(w) is int
                and type(h) is int
                and min(w, h) > 0
            ):
                size = [w, h]

        results.append({
            "page": i + 1,
            "provider_size": size,
            "line_count": len(texts),
            "lines": [
                {
                    "index": j,
                    "text": t,
                    "bbox": b,
                    "score": s,
                }
                for j, (t, b, s) in enumerate(
                    zip(texts, boxes, scores)
                )
            ],
        })

    return {
        "job_id": raw["job_id"],
        "pages": results,
    }


def recognize(
    folder,
    journal,
    *,
    execute=False,
):
    content, report = preflight(folder)

    if not execute:
        return {
            **report,
            "status": "preflight_only",
        }

    journal = Path(journal).resolve()

    production = DEFAULT_PATH.resolve()

    if (
        journal.name != "span-atlas-jobs.sqlite3"
        or journal == production
        or (
            journal.exists()
            and production.exists()
            and journal.samefile(production)
        )
    ):
        raise ValueError(
            "Use a dedicated span-atlas-jobs.sqlite3 journal"
        )

    original = paddle_resilient._optional_payload

    env = {
        "PADDLEOCR_MAX_PAGES_PER_JOB": "2",
        "PADDLEOCR_MAX_UPLOAD_MB": "1.5",
        "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL": "",
        "PADDLEOCR_SUBMIT_MAX_ATTEMPTS": "1",
    }

    with tempfile.TemporaryDirectory(
        prefix="quizpdf-span-ocr-"
    ) as tmp:

        path = Path(tmp) / "atlas.pdf"

        path.write_bytes(content)

        with (
            patch.dict(os.environ, env),
            patch.object(
                paddle_resilient,
                "_optional_payload",
                lambda model: (
                    dict(OPTIONS)
                    if model == MODEL
                    else original(model)
                ),
            ),
        ):
            _, raw = paddle_resilient.recognize_pdf(
                str(path),
                MODEL,
                user_id=OWNER,
                filename="atlas.pdf",
                journal_path=journal,
            )

    return {
        **report,
        "status": "recognized_unregistered",
        "model": MODEL,
        "requested_options": dict(OPTIONS),
        **summarize(
            raw,
            report["page_count"],
        ),
    }


def main():
    p = argparse.ArgumentParser(
        description=__doc__
    )

    p.add_argument(
        "--pack-dir",
        type=Path,
        required=True,
    )

    p.add_argument(
        "--journal",
        type=Path,
        required=True,
    )

    p.add_argument(
        "--env-file",
        type=Path,
    )

    p.add_argument(
        "--execute",
        action="store_true",
    )

    a = p.parse_args()

    if a.execute and a.env_file:

        from dotenv import load_dotenv

        if not a.env_file.is_file():
            p.error(
                "Environment file not found"
            )

        load_dotenv(
            a.env_file,
            override=False,
        )

    print(
        json.dumps(
            recognize(
                a.pack_dir,
                a.journal,
                execute=a.execute,
            ),
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
