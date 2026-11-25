"""Best-effort GET-only acquisition of images for a completed PP-OCRv6 page."""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import struct
import time
from urllib.parse import urlsplit

import cv2
import numpy as np
import requests

from .ocr_image_cache import (
    CachedImages,
    ImageCacheError,
    OCRImageCache,
    OCRPageRef,
    MAX_IMAGE_BYTES,
    MAX_PANEL_PIXELS,
    PNG_SIGNATURE,
)

logger = logging.getLogger(__name__)

HOST = re.compile(
    r"(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z][a-z0-9-]{0,62}"
)


class AcquisitionError(ValueError):
    """Failure reason contains no URL or credentials."""


def allowed_hosts_from_env():

    return frozenset(
        value.strip().lower()
        for value in os.getenv(
            "PADDLEOCR_IMAGE_ALLOWED_HOSTS",
            "",
        ).split(",")
        if value.strip()
    )


def _checked_url(url, hosts):

    if (
        not isinstance(url, str)
        or not 1 <= len(url) <= 8192
        or "\\" in url
        or any(
            not 33 <= ord(c) <= 126
            for c in url
        )
    ):

        raise AcquisitionError("invalid_url")

    try:

        parts = urlsplit(url)
        host = parts.hostname

        valid = (
            parts.scheme == "https"
            and host is not None
            and HOST.fullmatch(host)
            and host in hosts
            and parts.netloc.lower()
            in (host, host + ":443")
            and not parts.fragment
            and parts.path.startswith("/")
        )

    except ValueError:

        valid = False

    if not valid:

        raise AcquisitionError("untrusted_url")

    return url


def _fetch(url, deadline):

    if time.monotonic() >= deadline:

        raise AcquisitionError(
            "download_deadline"
        )

    # No auth, cookies shared across requests,
    # ambient proxy, or redirects.

    with requests.Session() as session:

        session.trust_env = False

        with session.get(
            url,

            stream=True,

            allow_redirects=False,

            timeout=(5, 10),

            headers={
                "Accept": (
                    "image/png, image/jpeg, "
                    "application/octet-stream"
                ),

                "Accept-Encoding": "identity",
            },

        ) as response:

            if response.status_code != 200:

                raise AcquisitionError(
                    f"http_{response.status_code}"
                )

            kind = (
                response.headers.get(
                    "Content-Type",
                    "",
                )
                .split(";", 1)[0]
                .strip()
                .lower()
            )

            if kind not in (
                "image/png",
                "image/jpeg",
                "application/octet-stream",
            ):

                raise AcquisitionError(
                    "unsupported_content_type"
                )

            if response.headers.get(
                "Content-Encoding",
                "identity",
            ).lower() != "identity":

                raise AcquisitionError(
                    "unsupported_content_encoding"
                )

            length = response.headers.get(
                "Content-Length"
            )

            if length is not None:

                if (
                    not length.isdecimal()
                    or not 1 <= int(length)
                    <= MAX_IMAGE_BYTES
                ):

                    raise AcquisitionError(
                        "invalid_content_length"
                    )

            data = bytearray()

            for chunk in response.iter_content(
                chunk_size=65536
            ):

                if time.monotonic() >= deadline:

                    raise AcquisitionError(
                        "download_deadline"
                    )

                if (
                    len(data) + len(chunk)
                    > MAX_IMAGE_BYTES
                ):

                    raise AcquisitionError(
                        "image_too_large"
                    )

                data.extend(chunk)

            if (
                length is not None
                and len(data) != int(length)
            ):

                raise AcquisitionError(
                    "truncated_image"
                )

    return bytes(data)


def _jpeg_size(data):

    # Read SOF dimensions before invoking OpenCV.
    # No EXIF rotation.

    i = 2

    sof = {
        0xC0,
        0xC1,
        0xC2,
    }

    while i < len(data):

        if data[i] != 0xFF:

            break

        while (
            i < len(data)
            and data[i] == 0xFF
        ):

            i += 1

        if i >= len(data):

            break

        marker = data[i]

        i += 1

        if (
            marker in (
                0xDA,
                0xD9,
                0xD8,
                0x00,
                0x01,
            )
            or 0xD0 <= marker <= 0xD7
        ):

            break

        if i + 2 > len(data):

            break

        size = int.from_bytes(
            data[i:i+2],
            "big",
        )

        if (
            size < 2
            or i + size > len(data)
        ):

            break

        if marker in sof and size >= 8:

            h, w = struct.unpack(
                ">HH",
                data[i+3:i+7],
            )

            return w, h

        i += size

    raise AcquisitionError(
        "invalid_jpeg_header"
    )


def _as_png(data, width, height):

    if not 1 <= len(data) <= MAX_IMAGE_BYTES:

        raise AcquisitionError(
            "invalid_image_length"
        )

    if data.startswith(PNG_SIGNATURE):

        if (
            len(data) < 33
            or data[8:16]
            != b"\x00\x00\x00\x0dIHDR"
        ):

            raise AcquisitionError(
                "invalid_png_header"
            )

        size = struct.unpack(
            ">II",
            data[16:24],
        )

    elif data.startswith(b"\xff\xd8"):

        size = _jpeg_size(data)

    else:

        raise AcquisitionError(
            "unsupported_image_format"
        )

    if size != (width, height):

        raise AcquisitionError(
            "image_dimensions_mismatch"
        )

    image = cv2.imdecode(
        np.frombuffer(
            data,
            np.uint8,
        ),

        cv2.IMREAD_COLOR
        | cv2.IMREAD_IGNORE_ORIENTATION,
    )

    if (
        image is None
        or image.shape
        != (height, width, 3)
    ):

        raise AcquisitionError(
            "image_decode_failed"
        )

    ok, encoded = cv2.imencode(
        ".png",
        image,
    )

    if (
        not ok
        or encoded.nbytes > MAX_IMAGE_BYTES
    ):

        raise AcquisitionError(
            "png_encode_failed"
        )

    return encoded.tobytes()


def acquire_images(
    journal,
    ref: OCRPageRef,
    cache: OCRImageCache,
    *,
    allowed_hosts=None,
) -> CachedImages | None:

    """Return cached or downloaded images.
    Never submit or poll an OCR job.
    """

    try:

        hit = cache.load(
            journal,
            ref,
        )

        if hit is not None:

            return hit

        hosts = (
            allowed_hosts_from_env()

            if allowed_hosts is None

            else frozenset(allowed_hosts)
        )

        if not hosts:

            return None

        if any(
            not isinstance(host, str)
            or not HOST.fullmatch(host)

            for host in hosts
        ):

            raise AcquisitionError(
                "invalid_host_configuration"
            )

        key = journal.batch_key(
            ref.start_page,

            ref.page_count,

            "PP-OCRv6",
        )

        result = journal.load_result(
            key,

            ref.claim_id,

            ref.job_id,

            page_count=ref.page_count,
        )

        if result is None:

            return None

        pages, info = result

        page = pages[
            ref.page_offset
        ]

        root = page.get(
            "pruned_result"
        )

        sizes = info.get("pages")

        if (
            not isinstance(root, dict)
            or not root.get("rec_boxes")
            or not isinstance(sizes, list)
            or len(sizes) != ref.page_count
            or not isinstance(
                sizes[ref.page_offset],
                dict,
            )
        ):

            return None

        size = sizes[
            ref.page_offset
        ]

        w, h = (
            size.get("width"),
            size.get("height"),
        )

        if (
            type(w) is not int
            or type(h) is not int
            or min(w, h) < 128
            or w * h > MAX_PANEL_PIXELS
        ):

            return None

        # Validate BOTH URLs before sending
        # the first GET.

        first = _checked_url(
            page.get("input_image_url"),
            hosts,
        )

        second = _checked_url(
            page.get("ocr_image_url"),
            hosts,
        )

        if first == second:

            raise AcquisitionError(
                "identical_image_urls"
            )

        deadline = (
            time.monotonic() + 45
        )

        input_png = _as_png(
            _fetch(
                first,
                deadline,
            ),

            w,
            h,
        )

        ocr_png = _as_png(
            _fetch(
                second,
                deadline,
            ),

            2 * w,
            h,
        )

        # Abort if the completed result was
        # replaced while downloading.

        if journal.load_result(
            key,

            ref.claim_id,

            ref.job_id,

            page_count=ref.page_count,

        ) != result:

            raise AcquisitionError(
                "result_changed"
            )

        cache.store(
            journal,
            ref,
            input_png,
            ocr_png,
        )

        return cache.load(
            journal,
            ref,
        )

    except AcquisitionError as exc:

        logger.info(
            "paddleocr:image_acquisition "
            "unavailable reason=%s",

            str(exc),
        )

    except (
        ImageCacheError,
        sqlite3.Error,
        requests.RequestException,
        OSError,
        ValueError,
        cv2.error,
        OverflowError,
    ) as exc:

        logger.info(
            "paddleocr:image_acquisition "
            "unavailable type=%s",

            type(exc).__name__,
        )

    return None