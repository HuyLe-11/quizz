"""Reduce generated crop sizes before writing to private Storage.

The Supabase bucket currently permits PNG and JPEG, not WebP. Large PNG
crops use high-quality JPEG, preserving image dimensions and avoiding
multiple-megabyte uploads when a full-page fallback is rendered.
"""

import logging

import cv2
import numpy as np

logger = logging.getLogger("quizpdf")

PNG_REENCODE_THRESHOLD = 512 * 1024
MAX_CROP_UPLOAD_BYTES = 1_500_000


class CropOptimizationError(ValueError):
    """An oversized crop cannot be prepared safely for upload."""


def prepare_crop_upload(contents: bytes) -> tuple[bytes, str, str]:
    """Return (file_bytes, filename_suffix, content_type).

    Small PNGs stay lossless. Large PNGs are converted to JPEG only if the
    result is both smaller and under the upload size target. No resizing is
    performed, so the original pixel dimensions remain unchanged.
    """
    if not isinstance(contents, bytes) or not contents:
        raise CropOptimizationError("Crop is empty or invalid.")

    if len(contents) <= PNG_REENCODE_THRESHOLD:
        return contents, ".png", "image/png"

    if not contents.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CropOptimizationError(
            "Oversized crop is not a valid PNG source."
        )

    decoded = cv2.imdecode(
        np.frombuffer(contents, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    if decoded is None:
        raise CropOptimizationError("Could not decode large PNG crop.")

    for quality in (92, 86, 80):
        success, encoded = cv2.imencode(
            ".jpg",
            decoded,
            [int(cv2.IMWRITE_JPEG_QUALITY), quality],
        )
        if not success:
            continue

        candidate = encoded.tobytes()

        if (
            len(candidate) < len(contents)
            and len(candidate) <= MAX_CROP_UPLOAD_BYTES
        ):
            logger.info(
                "cloud=crop_optimize format=jpeg quality=%d "
                "original_bytes=%d upload_bytes=%d",
                quality,
                len(contents),
                len(candidate),
            )
            return candidate, ".jpg", "image/jpeg"

    if len(contents) <= MAX_CROP_UPLOAD_BYTES:
        return contents, ".png", "image/png"

    raise CropOptimizationError(
        "Crop remains too large after JPEG optimization. "
        "Check full-page fallback or PDF crop boundaries."
    )
