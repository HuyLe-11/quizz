"""Prepare crop images for private Storage without changing source crops."""

import logging

import cv2
import numpy as np

logger = logging.getLogger("quizpdf")

PNG_REENCODE_THRESHOLD = 512 * 1024
MAX_CROP_UPLOAD_BYTES = 1_500_000
JPEG_QUALITIES = (92, 86, 80)
RESIZE_FACTOR = 0.8
MAX_RESIZE_STEPS = 6


class CropOptimizationError(ValueError):
    """An oversized crop cannot be prepared safely for upload."""


def prepare_crop_upload(
    contents: bytes,
) -> tuple[bytes, str, str]:
    """Keep small PNGs, prefer original-size JPEG, then downsample if needed."""

    if not isinstance(contents, bytes) or not contents:
        raise CropOptimizationError(
            "Crop is empty or invalid."
        )

    if len(contents) <= PNG_REENCODE_THRESHOLD:
        return contents, ".png", "image/png"

    if not contents.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CropOptimizationError(
            "Oversized crop is not a valid PNG source."
        )

    try:
        decoded = cv2.imdecode(
            np.frombuffer(contents, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )

        if decoded is None:
            raise CropOptimizationError(
                "Could not decode large PNG crop."
            )

        height, width = decoded.shape[:2]

        for step in range(MAX_RESIZE_STEPS + 1):
            scale = RESIZE_FACTOR ** step

            new_width = max(
                1,
                round(width * scale),
            )

            new_height = max(
                1,
                round(height * scale),
            )

            image = decoded if step == 0 else cv2.resize(
                decoded,
                (new_width, new_height),
                interpolation=cv2.INTER_AREA,
            )

            for quality in JPEG_QUALITIES:
                success, encoded = cv2.imencode(
                    ".jpg",
                    image,
                    [
                        int(cv2.IMWRITE_JPEG_QUALITY),
                        quality,
                    ],
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
                        "original_bytes=%d upload_bytes=%d "
                        "original_size=%dx%d upload_size=%dx%d resized=%s",
                        quality,
                        len(contents),
                        len(candidate),
                        width,
                        height,
                        new_width,
                        new_height,
                        step > 0,
                    )

                    return candidate, ".jpg", "image/jpeg"

            if (
                step == 0
                and len(contents) <= MAX_CROP_UPLOAD_BYTES
            ):
                return contents, ".png", "image/png"

    except cv2.error as exc:
        raise CropOptimizationError(
            "Could not optimize crop image."
        ) from exc

    raise CropOptimizationError(
        "Crop remains too large after bounded JPEG "
        "and resize optimization. "
        "Check full-page fallback or PDF crop boundaries."
    )