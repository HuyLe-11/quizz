"""Bounded local cache of PP-OCRv6 image pairs; no network or OCR calls."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import struct
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .ocr_job_journal import OCRJobJournal

DEFAULT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "storage" / "ocr-images" / "images.sqlite3"
)
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PANEL_PIXELS = 4_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ImageCacheError(ValueError):
    """Invalid image or unavailable binding."""


@dataclass(frozen=True)
class OCRPageRef:
    start_page: int
    page_count: int
    page_offset: int
    claim_id: str
    job_id: str


@dataclass(frozen=True)
class CachedImages:
    input_image: np.ndarray
    ocr_image: np.ndarray


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _binding(journal: OCRJobJournal, ref: OCRPageRef):
    if (
        type(ref.page_offset) is not int
        or type(ref.page_count) is not int
        or not 0 <= ref.page_offset < ref.page_count
        or not isinstance(ref.claim_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", ref.claim_id)
        or not isinstance(ref.job_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", ref.job_id)
    ):
        raise ImageCacheError("Invalid OCR page reference")

    batch_key = journal.batch_key(
        ref.start_page,
        ref.page_count,
        "PP-OCRv6",
    )

    result = journal.load_result(
        batch_key,
        ref.claim_id,
        ref.job_id,
        page_count=ref.page_count,
    )

    if result is None:
        return None

    pages, info = result

    page = pages[ref.page_offset]

    root = page.get("pruned_result")
    sizes = info.get("pages")

    if (
        not isinstance(root, dict)
        or not root.get("rec_boxes")
        or not isinstance(sizes, list)
        or len(sizes) != ref.page_count
        or not isinstance(sizes[ref.page_offset], dict)
    ):
        return None

    size = sizes[ref.page_offset]

    width = size.get("width")
    height = size.get("height")

    if (
        type(width) is not int
        or type(height) is not int
        or min(width, height) < 128
        or width * height > MAX_PANEL_PIXELS
    ):
        return None

    identity = {
        "version": 1,
        "scope": journal.scope,
        "batch_key": batch_key,
        "claim_id": ref.claim_id,
        "job_id": ref.job_id,
        "page_offset": ref.page_offset,
        "width": width,
        "height": height,
        "recognition": root,
    }

    key = _digest(
        json.dumps(
            identity,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    )

    return key, width, height


def _decode_png(data, width, height):

    # Check PNG dimensions BEFORE invoking the decoder.

    if (
        not isinstance(data, bytes)
        or not 33 <= len(data) <= MAX_IMAGE_BYTES
        or data[:8] != PNG_SIGNATURE
        or data[8:16] != b"\x00\x00\x00\x0dIHDR"
        or struct.unpack(
            ">II", data[16:24]
        ) != (width, height)
    ):
        raise ImageCacheError(
            "Invalid PNG or unexpected dimensions"
        )

    try:

        image = cv2.imdecode(
            np.frombuffer(
                data,
                dtype=np.uint8,
            ),
            cv2.IMREAD_COLOR,
        )

    except cv2.error as exc:

        raise ImageCacheError(
            "PNG decode failed"
        ) from exc

    if (
        image is None
        or image.shape != (height, width, 3)
    ):
        raise ImageCacheError(
            "Unexpected decoded image geometry"
        )

    return image


class OCRImageCache:

    def __init__(
        self,
        path: Path = DEFAULT_PATH,
        *,
        max_bytes: int = 128 * 1024 * 1024,
        ttl_seconds: int = 7 * 24 * 3600,
    ):

        if (
            type(max_bytes) is not int
            or max_bytes < 1
            or type(ttl_seconds) is not int
            or ttl_seconds < 1
        ):
            raise ValueError(
                "Invalid image cache limits"
            )

        self.path = Path(path)

        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

        if os.name != "nt":
            self.path.parent.chmod(0o700)

        with self._transaction() as conn:

            conn.execute("""
                CREATE TABLE IF NOT EXISTS image_pairs (
                    cache_key TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    input_png BLOB NOT NULL,
                    ocr_png BLOB NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    ocr_sha256 TEXT NOT NULL
                )
            """)

        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def _transaction(self):

        with closing(
            sqlite3.connect(
                self.path,
                timeout=10,
            )
        ) as conn:

            conn.execute(
                "PRAGMA synchronous=FULL"
            )

            with conn:

                conn.execute(
                    "BEGIN IMMEDIATE"
                )

                yield conn

    def _prune(
        self,
        conn,
        now,
        protected_key="",
    ):

        conn.execute(
            """
            DELETE FROM image_pairs
            WHERE created_at <= ?
            """,
            (
                now - self.ttl_seconds,
            ),
        )

        total = conn.execute("""
            SELECT COALESCE(
                SUM(
                    length(input_png)
                    + length(ocr_png)
                ),
                0
            )
            FROM image_pairs
        """).fetchone()[0]

        if total <= self.max_bytes:
            return

        rows = conn.execute(
            """
            SELECT
                cache_key,
                length(input_png)
                + length(ocr_png)
            FROM image_pairs
            WHERE cache_key != ?
            ORDER BY created_at, cache_key
            """,
            (protected_key,),
        ).fetchall()

        for key, size in rows:

            if total <= self.max_bytes:
                break

            conn.execute(
                """
                DELETE FROM image_pairs
                WHERE cache_key = ?
                """,
                (key,),
            )

            total -= size

    def prune(self):
        """Enforce age and logical BLOB budget without contacting PaddleOCR."""

        with self._transaction() as conn:

            self._prune(
                conn,
                time.time(),
            )

    def store(
        self,
        journal,
        ref,
        input_png: bytes,
        ocr_png: bytes,
    ):
        """Store a trusted provider image pair for a completed cached page.

        The caller is responsible for obtaining both images from that job/page.
        Registration must still verify PDF identity and geometric support.
        """

        binding = _binding(
            journal,
            ref,
        )

        if binding is None:

            raise ImageCacheError(
                "Completed OCR result unavailable"
            )

        key, width, height = binding

        _decode_png(
            input_png,
            width,
            height,
        )

        _decode_png(
            ocr_png,
            2 * width,
            height,
        )

        if (
            len(input_png)
            + len(ocr_png)
            > self.max_bytes
        ):
            raise ImageCacheError(
                "Image pair exceeds cache budget"
            )

        with self._transaction() as conn:

            now = time.time()

            conn.execute(
                """
                INSERT INTO image_pairs
                VALUES (?, ?, ?, ?, ?, ?)

                ON CONFLICT(cache_key)
                DO UPDATE SET
                    created_at = excluded.created_at,
                    input_png = excluded.input_png,
                    ocr_png = excluded.ocr_png,
                    input_sha256 = excluded.input_sha256,
                    ocr_sha256 = excluded.ocr_sha256
                """,
                (
                    key,
                    now,
                    input_png,
                    ocr_png,
                    _digest(input_png),
                    _digest(ocr_png),
                ),
            )

            self._prune(
                conn,
                now,
                key,
            )

    def load(
        self,
        journal,
        ref,
    ) -> CachedImages | None:

        binding = _binding(
            journal,
            ref,
        )

        if binding is None:
            return None

        key, width, height = binding

        with self._transaction() as conn:

            row = conn.execute(
                """
                SELECT
                    input_png,
                    ocr_png,
                    input_sha256,
                    ocr_sha256
                FROM image_pairs

                WHERE cache_key = ?
                  AND created_at > ?
                  AND typeof(input_png) = 'blob'
                  AND typeof(ocr_png) = 'blob'
                  AND length(input_png) BETWEEN 33 AND ?
                  AND length(ocr_png) BETWEEN 33 AND ?
                """,
                (
                    key,
                    time.time() - self.ttl_seconds,
                    MAX_IMAGE_BYTES,
                    MAX_IMAGE_BYTES,
                ),
            ).fetchone()

        if row is None:
            return None

        first, second, sha1, sha2 = row

        if (
            _digest(first) != sha1
            or _digest(second) != sha2
        ):
            return None

        try:

            return CachedImages(
                _decode_png(
                    first,
                    width,
                    height,
                ),
                _decode_png(
                    second,
                    2 * width,
                    height,
                ),
            )

        except ImageCacheError:
            return None