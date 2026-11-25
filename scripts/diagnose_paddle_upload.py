"""Diagnose PaddleOCR PDF submission without automatic retries."""

import argparse
import json
import os
import sys
import time

from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from requests.utils import get_environ_proxies


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose PaddleOCR upload timeouts."
    )

    parser.add_argument(
        "pdf",
        type=Path,
        help="Path to a PDF file.",
    )

    parser.add_argument(
        "--model",
        default="PaddleOCR-VL-1.6",
        choices=["PaddleOCR-VL-1.6", "PP-OCRv6"],
    )

    parser.add_argument(
        "--socket-timeout",
        type=float,
        default=60.0,
    )

    parser.add_argument(
        "--read-timeout",
        type=float,
        default=180.0,
    )

    args = parser.parse_args()

    load_dotenv(
        Path(__file__).resolve().parents[1]
        / "backend"
        / ".env"
    )

    pdf = args.pdf.resolve()

    if not pdf.is_file():
        print(f"PDF not found: {pdf}")
        return 1

    if args.socket_timeout <= 0 or args.read_timeout <= 0:
        print("Timeout values must be positive.")
        return 1

    if not pdf.read_bytes()[:5] == b"%PDF-":
        print("File does not start with a PDF header.")
        return 1

    token = os.getenv(
        "PADDLEOCR_ACCESS_TOKEN",
        "",
    ).strip()

    if not token:
        print("Missing PADDLEOCR_ACCESS_TOKEN.")
        return 1

    base_url = os.getenv(
        "PADDLEOCR_BASE_URL",
        "https://paddleocr.aistudio-app.com",
    ).rstrip("/")

    job_url = os.getenv(
        "JOB_URL",
        "",
    ).strip() or f"{base_url}/api/v2/ocr/jobs"

    if urlparse(job_url).scheme != "https":
        print("Only HTTPS endpoints are supported.")
        return 1

    proxy_schemes = sorted(
        get_environ_proxies(job_url)
    )

    print(f"PDF: {pdf.name}", flush=True)
    print(f"Bytes: {pdf.stat().st_size}", flush=True)

    print(
        f"Endpoint: {urlparse(job_url).hostname}",
        flush=True,
    )

    print(
        "Environment proxy schemes: "
        + (
            ", ".join(proxy_schemes)
            if proxy_schemes
            else "none"
        ),
        flush=True,
    )

    print(
        "Timeouts: "
        f"socket={args.socket_timeout}s, "
        f"read={args.read_timeout}s",
        flush=True,
    )

    print(
        "Submitting one OCR job. "
        "No automatic POST retries.",
        flush=True,
    )

    start = time.monotonic()

    try:
        with requests.Session() as session:
            session.mount(
                "https://",
                HTTPAdapter(max_retries=0),
            )

            with pdf.open("rb") as source:
                response = session.post(
                    job_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                    },
                    data={
                        "model": args.model,
                        "optionalPayload": json.dumps({}),
                    },
                    files={
                        "file": (
                            pdf.name,
                            source,
                            "application/pdf",
                        ),
                    },
                    timeout=(
                        args.socket_timeout,
                        args.read_timeout,
                    ),
                )

    except requests.RequestException as exc:
        elapsed = time.monotonic() - start

        print(
            f"POST_FAILED elapsed={elapsed:.2f}s",
            flush=True,
        )

        print(
            f"Exception: {type(exc).__name__}",
            flush=True,
        )

        print(
            "No job ID was received. "
            "The server may still have received the request.",
            flush=True,
        )

        return 2

    elapsed = time.monotonic() - start

    print(
        f"HTTP_STATUS={response.status_code}",
        flush=True,
    )

    print(
        f"POST_ELAPSED={elapsed:.2f}s",
        flush=True,
    )

    if not response.ok:
        print(
            "PaddleOCR rejected the request. "
            "Inspect the HTTP status and server logs.",
            flush=True,
        )
        return 3

    try:
        payload = response.json()
    except ValueError:
        print("PaddleOCR returned invalid JSON.")
        return 4

    data = (
        payload.get("data")
        if isinstance(payload, dict)
        else None
    )

    job_id = (
        data.get("jobId")
        if isinstance(data, dict)
        else None
    )

    if not job_id:
        print(
            "POST completed but no job ID was returned.",
            flush=True,
        )
        return 5

    print(
        f"JOB_CREATED={job_id}",
        flush=True,
    )

    print(
        "The OCR job was submitted. "
        "This diagnostic does not poll or download results.",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
