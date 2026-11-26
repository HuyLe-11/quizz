"""Diagnose PaddleOCR-to-PDF registration without submitting OCR jobs."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit

import pymupdf

from .engine.ocr_job_journal import OCRJobJournal, DEFAULT_PATH
from .engine.ocr_image_cache import OCRImageCache, OCRPageRef, DEFAULT_PATH as IMAGE_PATH
from .engine.ocr_image_acquisition import acquire_images, allowed_hosts_from_env
from .engine.ocr_image_registration import register_page, RegistrationError
from .paddle_word_geometry import read_job, page_report


def _same_path(a, b):
    return a.resolve() == b.resolve() or (
        a.exists() and b.exists() and a.samefile(b)
    )


def _host(value):
    try:
        return urlsplit(value).hostname if isinstance(value, str) else None
    except ValueError:
        return None


def probe_points(registration, bbox):
    x0, y0, x1, y1 = bbox
    distances = []

    for fx, fy in (
        (.2, .2),
        (.8, .2),
        (.5, .5),
        (.2, .8),
        (.8, .8),
    ):
        x = x0 + fx * (x1 - x0)
        y = y0 + fy * (y1 - y0)

        p = registration.map_point(x, y)

        if p is None:
            return {
                "status": "local_support_missing",
                "points": len(distances),
            }

        distances.append(
            math.hypot(p.x - x, p.y - y)
        )

    worst = max(distances)

    return {
        "status": (
            "near_identity_candidate"
            if worst <= 2
            else "nonidentity"
        ),
        "points": 5,
        "max_displacement_px": round(worst, 3),
    }


def inspect(
    pdf,
    expected_sha,
    journal_path,
    job_id,
    *,
    matches=(),
    fetch_images=False,
):
    pdf = Path(pdf).resolve()
    journal_path = Path(journal_path).resolve()

    if not re.fullmatch(
        r"[0-9a-f]{64}",
        expected_sha,
    ):
        raise ValueError(
            "Invalid expected SHA-256"
        )

    if (
        not journal_path.is_file()
        or _same_path(journal_path, DEFAULT_PATH)
    ):
        raise ValueError(
            "Use the existing experimental journal"
        )

    image_path = journal_path.with_name(
        "registration-images.sqlite3"
    )

    if any(
        _same_path(image_path, p)
        for p in (
            journal_path,
            DEFAULT_PATH,
            IMAGE_PATH,
            pdf,
        )
    ):
        raise ValueError(
            "Unsafe image cache path"
        )

    source = pdf.read_bytes()

    if (
        hashlib.sha256(source).hexdigest()
        != expected_sha
    ):
        raise ValueError(
            "PDF SHA-256 mismatch"
        )

    with tempfile.TemporaryDirectory(
        prefix="quizpdf-reg-"
    ) as temp:

        snapshot = (
            Path(temp) / "jobs.sqlite3"
        )

        with closing(
            sqlite3.connect(
                journal_path.as_uri() + "?mode=ro",
                uri=True,
            )
        ) as src:

            with closing(
                sqlite3.connect(snapshot)
            ) as dst:

                src.backup(dst)

        data = read_job(
            snapshot,
            job_id,
        )

        with closing(
            sqlite3.connect(snapshot)
        ) as conn:

            rows = conn.execute(
                """
                SELECT scope, batch_key, claim_id
                FROM ocr_jobs
                WHERE job_id=?
                  AND state='completed'
                LIMIT 2
                """,
                (job_id,),
            ).fetchall()

        key = OCRJobJournal.batch_key(
            0,
            1,
            "PP-OCRv6",
        )

        if (
            len(rows) != 1
            or rows[0][1] != key
        ):
            raise ValueError(
                "Expected one PP-OCRv6 batch for page 1"
            )

        scope, _, claim = rows[0]

        page = data["pages"][0]

        info = data["data_info"].get(
            "pages"
        )

        if (
            not isinstance(info, list)
            or len(info) != 1
            or not isinstance(info[0], dict)
        ):
            raise ValueError(
                "Missing page dimensions"
            )

        w = info[0].get("width")
        h = info[0].get("height")

        if (
            type(w) is not int
            or type(h) is not int
            or min(w, h) < 128
            or w * h > 4_000_000
        ):
            raise ValueError(
                "Unsupported dimensions"
            )

        report = page_report(
            page,
            (w, h),
            matches=matches,
        )

        root = page["pruned_result"]

        heights = [
            b[3] - b[1]
            for b in root["rec_boxes"]
            if (
                isinstance(b, list)
                and len(b) == 4
                and all(type(v) is int for v in b)
                and 0 <= b[1] < b[3] <= h
            )
        ]

        if not heights:
            raise ValueError(
                "Missing line heights"
            )

        unit = float(
            median(heights)
        )

        result = {
            "job_id": job_id,
            "pdf_sha256": expected_sha,
            "size": [w, h],
            "line_status_counts": report[
                "line_status_counts"
            ],
            "input_image_host": _host(
                page.get("input_image_url")
            ),
            "ocr_image_host": _host(
                page.get("ocr_image_url")
            ),
            "fetch_images": fetch_images,
            "unit": unit,
            "samples": [],
        }

        with pymupdf.open(
            stream=source,
            filetype="pdf",
        ) as doc:

            if (
                doc.needs_pass
                or len(doc) != 1
                or doc[0].rotation
            ):
                raise ValueError(
                    "Expected one unrotated PDF page"
                )

            journal = OCRJobJournal(
                scope,
                snapshot,
            )

            ref = OCRPageRef(
                0,
                1,
                0,
                claim,
                job_id,
            )

            cache = OCRImageCache(
                image_path
            )

            images = acquire_images(
                journal,
                ref,
                cache,
                allowed_hosts=(
                    allowed_hosts_from_env()
                    if fetch_images
                    else ()
                ),
            )

            if images is None:
                return {
                    **result,
                    "status": "ocr_images_unavailable",
                }

            try:
                reg = register_page(
                    doc[0],
                    images.input_image,
                    images.ocr_image,
                    width=w,
                    height=h,
                    unit=unit,
                )

            except RegistrationError as exc:

                return {
                    **result,
                    "status": "registration_rejected",
                    "reason": str(exc),
                }

            result.update(
                status="registration_established",
                mutual_matches=reg.mutual_matches,
                inliers=reg.inliers,
                input_mae=reg.input_mae,
            )

            for line in report["samples"]:

                for span in line["spans"]:

                    text = span["text"]

                    if (
                        not text.strip()
                        or (
                            matches
                            and text not in matches
                        )
                    ):
                        continue

                    if len(result["samples"]) >= 64:
                        return result

                    result["samples"].append({
                        "line_index": line["line_index"],
                        "text": text,
                        "bbox": span["bbox"],
                        **probe_points(
                            reg,
                            span["bbox"],
                        ),
                    })

            return result


def main():
    p = argparse.ArgumentParser(
        description=__doc__
    )

    p.add_argument(
        "--pdf",
        type=Path,
        required=True,
    )

    p.add_argument(
        "--sha256",
        required=True,
    )

    p.add_argument(
        "--journal",
        type=Path,
        required=True,
    )

    p.add_argument(
        "--job-id",
        required=True,
    )

    p.add_argument(
        "--match",
        action="append",
        default=[],
    )

    p.add_argument(
        "--fetch-images",
        action="store_true",
    )

    p.add_argument(
        "--env-file",
        type=Path,
    )

    args = p.parse_args()

    if args.env_file:

        from dotenv import load_dotenv

        if not args.env_file.is_file():
            p.error(
                "Environment file not found"
            )

        load_dotenv(
            args.env_file,
            override=False,
        )

    print(
        json.dumps(
            inspect(
                args.pdf,
                args.sha256,
                args.journal,
                args.job_id,
                matches=args.match,
                fetch_images=args.fetch_images,
            ),
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
