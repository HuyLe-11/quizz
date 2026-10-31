#!/usr/bin/env python3
"""Call PaddleOCR's hosted API for a local PDF and print a safe summary."""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / "backend" / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from backend.engine.paddle_client import recognize_pdf  # noqa: E402


MODELS = ("PaddleOCR-VL-1.6", "PP-OCRv6")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Path to a local PDF file")
    parser.add_argument(
        "--model",
        choices=MODELS,
        default=os.getenv("MODEL", "PaddleOCR-VL-1.6"),
        help="PaddleOCR model (defaults to MODEL in backend/.env)",
    )
    parser.add_argument(
        "--save-response",
        type=Path,
        help="Optionally save the full JSON response to this path",
    )
    args = parser.parse_args()

    if not args.pdf.is_file():
        parser.error(f"PDF file does not exist: {args.pdf}")
    if args.pdf.suffix.lower() != ".pdf":
        parser.error("Input file must have a .pdf extension")
    if not os.getenv("PADDLEOCR_ACCESS_TOKEN"):
        parser.error("PADDLEOCR_ACCESS_TOKEN is missing from backend/.env")

    try:
        text, response = recognize_pdf(str(args.pdf.resolve()), args.model)
    except Exception as exc:
        print(f"PaddleOCR request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    pages = response.get("pages", [])
    job_id = response.get("job_id") or response.get("jobId") or "unavailable"
    print(f"PaddleOCR API: success\nModel: {args.model}\nJob ID: {job_id}")
    print(f"Pages reported: {len(pages) if isinstance(pages, list) else 'unavailable'}")
    print(f"Extracted text characters: {len(text)}")
    if text:
        print("\nText preview (up to 1200 chars):\n" + text[:1200])
    else:
        print("No page text was found in the parsed response.")

    if args.save_response:
        args.save_response.parent.mkdir(parents=True, exist_ok=True)
        args.save_response.write_text(
            json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Full response saved to: {args.save_response}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
