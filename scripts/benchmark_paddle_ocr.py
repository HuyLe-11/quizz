"""Benchmark the existing PaddleOCR pipeline without cloud persistence.

This script sends real requests to the configured PaddleOCR API.
It never writes exam data or cropped images to Supabase.
"""

import argparse
import hashlib
import logging
import os
import sys
import time

from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.engine.paddle_client import recognize_pdf


def run_benchmark(
    pdf: Path,
    model: str,
    pages_per_job: int,
) -> dict:
    """Run one real OCR attempt and collect basic metrics."""
    previous = os.environ.get(
        "PADDLEOCR_MAX_PAGES_PER_JOB"
    )

    os.environ["PADDLEOCR_MAX_PAGES_PER_JOB"] = str(
        pages_per_job
    )

    started = time.monotonic()

    try:
        text, raw = recognize_pdf(
            str(pdf),
            model,
        )
    finally:
        if previous is None:
            os.environ.pop(
                "PADDLEOCR_MAX_PAGES_PER_JOB",
                None,
            )
        else:
            os.environ["PADDLEOCR_MAX_PAGES_PER_JOB"] = (
                previous
            )

    elapsed = time.monotonic() - started

    pages = raw.get("pages", [])
    batches = raw.get("batches", [])

    if batches:
        job_count = sum(
            len(batch.get("jobs", []))
            for batch in batches
        )
    else:
        job_count = 1

    return {
        "elapsed_seconds": round(elapsed, 2),
        "recognized_pages": len(pages),
        "characters": len(text),
        "ocr_jobs": job_count,
        "model": model,
        "pages_per_job": pages_per_job,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure PaddleOCR performance without "
            "saving data to Supabase."
        )
    )

    parser.add_argument(
        "pdf",
        type=Path,
        help="Path to the PDF file.",
    )

    parser.add_argument(
        "--model",
        choices=[
            "PaddleOCR-VL-1.6",
            "PP-OCRv6",
        ],
        default="PaddleOCR-VL-1.6",
    )

    parser.add_argument(
        "--pages-per-job",
        type=int,
        default=5,
    )

    args = parser.parse_args(argv)

    load_dotenv(
        PROJECT_ROOT / "backend" / ".env"
    )

    pdf = args.pdf.resolve()

    if not pdf.is_file():
        print("ERROR: PDF file does not exist.")
        return 1

    if not 1 <= args.pages_per_job <= 20:
        print(
            "ERROR: pages-per-job must be "
            "between 1 and 20."
        )
        return 1

    if not pdf.read_bytes()[:5] == b"%PDF-":
        print("ERROR: Invalid PDF header.")
        return 1

    if not os.getenv("PADDLEOCR_ACCESS_TOKEN"):
        print("ERROR: Missing PaddleOCR access token.")
        return 1

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )

    file_hash = hashlib.sha256(
        pdf.read_bytes()
    ).hexdigest()[:16]

    print("\nPaddleOCR benchmark")
    print("-------------------")
    print(f"File: {pdf.name}")
    print(f"SHA256 prefix: {file_hash}")
    print(f"Size: {pdf.stat().st_size} bytes")
    print(f"Model: {args.model}")
    print(
        f"Maximum pages per job: "
        f"{args.pages_per_job}"
    )
    print(
        "Cloud persistence: DISABLED"
    )
    print()

    try:
        result = run_benchmark(
            pdf,
            args.model,
            args.pages_per_job,
        )
    except Exception as exc:
        print("\nBenchmark failed.")
        print(
            f"Error type: {type(exc).__name__}"
        )
        print(
            f"Details: {str(exc)[:400]}"
        )
        return 2

    print("\nBenchmark completed")
    print("-------------------")

    for key, value in result.items():
        print(f"{key}: {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
