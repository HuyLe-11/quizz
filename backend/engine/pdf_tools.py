# PDF question crops and conservative red-ink answer-mark detection.
from __future__ import annotations

import hashlib
import json
import logging
import math
import os

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np

from backend.engine.pdf_layout import (
    locate,
    question_segments,
)

from backend.engine.pdf_render_policy import (
    plan_crop_render,
)


logger = logging.getLogger("quizpdf")


@dataclass(frozen=True)
class RedMarkConfig:
    dpi: int = 200
    hue_low_max: int = 10
    hue_high_min: int = 170
    saturation_min: int = 75
    value_min: int = 45
    morphology_kernel: int = 3
    min_component_pixels: int = 8
    min_red_pixels: int = 24
    max_distance_points: float = 32.0
    crop_padding_points: float = 14.0
    high_confidence: float = 0.76
    medium_confidence: float = 0.42
    min_score_margin: float = 0.14

    @classmethod
    def from_environment(cls) -> "RedMarkConfig":
        return cls(
            dpi=int(
                os.getenv("PDF_RENDER_DPI", "200")
            ),

            hue_low_max=int(
                os.getenv("RED_HUE_LOW_MAX", "10")
            ),

            hue_high_min=int(
                os.getenv("RED_HUE_HIGH_MIN", "170")
            ),

            saturation_min=int(
                os.getenv("RED_SATURATION_MIN", "75")
            ),

            value_min=int(
                os.getenv("RED_VALUE_MIN", "45")
            ),

            morphology_kernel=max(
                1,
                int(
                    os.getenv(
                        "RED_MORPHOLOGY_KERNEL",
                        "3",
                    )
                ),
            ),

            min_component_pixels=int(
                os.getenv(
                    "RED_MIN_COMPONENT_PIXELS",
                    "8",
                )
            ),

            min_red_pixels=int(
                os.getenv("RED_MIN_PIXELS", "24")
            ),

            max_distance_points=float(
                os.getenv(
                    "RED_MAX_DISTANCE_POINTS",
                    "32",
                )
            ),

            crop_padding_points=float(
                os.getenv(
                    "PDF_CROP_PADDING_POINTS",
                    "14",
                )
            ),

            high_confidence=float(
                os.getenv(
                    "RED_HIGH_CONFIDENCE",
                    "0.76",
                )
            ),

            medium_confidence=float(
                os.getenv(
                    "RED_MEDIUM_CONFIDENCE",
                    "0.42",
                )
            ),

            min_score_margin=float(
                os.getenv(
                    "RED_MIN_SCORE_MARGIN",
                    "0.14",
                )
            ),
        )

    @property
    def fingerprint(self) -> str:
        payload = {
            "algorithm_version": 3,
            **asdict(self),
        }

        return hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
            ).encode()
        ).hexdigest()[:16]


def _render_segment(
    page: fitz.Page,
    clip: tuple[float, float, float, float],
    dpi: int,
) -> np.ndarray:

    rect = fitz.Rect(*clip) & page.rect

    if (
        rect.is_empty
        or rect.width <= 0
        or rect.height <= 0
    ):
        raise ValueError(
            "Invalid PDF crop rectangle"
        )

    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(
            dpi / 72,
            dpi / 72,
        ),
        clip=rect,
        alpha=False,
        annots=True,
    )

    image = np.frombuffer(
        pixmap.samples,
        dtype=np.uint8,
    ).reshape(
        pixmap.height,
        pixmap.width,
        pixmap.n,
    )

    return cv2.cvtColor(
        image,
        cv2.COLOR_RGB2BGR,
    )


def _join_images(
    images: list[np.ndarray],
) -> np.ndarray:

    max_width = max(
        image.shape[1]
        for image in images
    )

    normalized = []

    for image in images:
        if image.shape[1] < max_width:

            image = cv2.copyMakeBorder(
                image,
                0,
                0,
                0,
                max_width - image.shape[1],
                cv2.BORDER_CONSTANT,
                value=(255, 255, 255),
            )

        normalized.append(image)

    return cv2.vconcat(normalized)


def crop_question_pdf(
    pdf_path: str | Path,
    question_number: int,
    page_number: int | None,
    output_path: str | Path,
    ocr_pages: list[dict[str, Any]] | None = None,
    config: RedMarkConfig | None = None,
) -> dict[str, Any]:

    config = (
        config
        or RedMarkConfig.from_environment()
    )

    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with fitz.open(pdf_path) as document:

        first_page = (
            page_number
            if page_number is not None
            else 1
        ) - 1

        segments, located = question_segments(
            document,
            question_number,
            first_page,
            config.crop_padding_points,
            ocr_pages,
        )

        # Select one common DPI for all segments.
        # This changes rasterization only, not PDF coordinates.
        plan = plan_crop_render(
            document,
            segments,
            config.dpi,
        )

        logger.info(
            "pdf=crop_render question=%d "
            "mode=%s requested_dpi=%d "
            "actual_dpi=%d size=%dx%d pixels=%d",
            question_number,
            plan.mode,
            config.dpi,
            plan.dpi,
            plan.width,
            plan.height,
            plan.pixels,
        )

        images = [
            _render_segment(
                document[index],
                clip,
                plan.dpi,
            )
            for index, clip in segments
        ]

        image = _join_images(images)

        # Use Python file writing to support
        # Unicode paths on Windows.
        success, encoded = cv2.imencode(
            ".png",
            image,
        )

        if not success:
            raise OSError(
                "Could not encode cropped question image"
            )

        output_path.write_bytes(
            encoded.tobytes()
        )

        coordinates = [
            {
                "page_number": index + 1,

                "bbox": [
                    round(value, 2)
                    for value in clip
                ],
            }

            for index, clip in segments
        ]

        return {
            "image_path": str(output_path),

            "page_number": first_page + 1,

            "crop_coordinates": coordinates,

            "crop_status": "success",

            "source": (
                located["source"]
                if located
                else "full_page_fallback"
            ),
        }


def _expanded(
    box: tuple[float, float, float, float],
    margin: float,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:

    return (
        max(0, int(box[0] - margin)),
        max(0, int(box[1] - margin)),
        min(width, int(box[2] + margin)),
        min(height, int(box[3] + margin)),
    )


def _rect_distance(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:

    dx = max(
        first[0] - second[2],
        second[0] - first[2],
        0,
    )

    dy = max(
        first[1] - second[3],
        second[1] - first[3],
        0,
    )

    return math.hypot(dx, dy)


@lru_cache(maxsize=2)
def _cached_page_red_data(
    pdf_path: str,
    modified_ns: int,
    file_size: int,
    page_index: int,
    config: RedMarkConfig,
) -> tuple[
    float,
    float,
    np.ndarray,
    list[tuple[int, int, int, int, int]],
]:

    with fitz.open(pdf_path) as document:

        page = document[page_index]

        page_width = page.rect.width

        page_height = page.rect.height

        # Keep the existing red-mark detector DPI.
        # Its pixel thresholds and coordinate transforms
        # depend on this resolution.
        image = _render_segment(
            page,
            (
                0,
                0,
                page_width,
                page_height,
            ),
            config.dpi,
        )

    hsv = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2HSV,
    )

    lower = cv2.inRange(
        hsv,

        np.array([
            0,
            config.saturation_min,
            config.value_min,
        ]),

        np.array([
            config.hue_low_max,
            255,
            255,
        ]),
    )

    upper = cv2.inRange(
        hsv,

        np.array([
            config.hue_high_min,
            config.saturation_min,
            config.value_min,
        ]),

        np.array([
            179,
            255,
            255,
        ]),
    )

    red_mask = cv2.bitwise_or(
        lower,
        upper,
    )

    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (
            config.morphology_kernel,
            config.morphology_kernel,
        ),
    )

    red_mask = cv2.morphologyEx(
        red_mask,
        cv2.MORPH_OPEN,
        kernel,
    )

    red_mask = cv2.morphologyEx(
        red_mask,
        cv2.MORPH_CLOSE,
        kernel,
    )

    count, _, stats, _ = (
        cv2.connectedComponentsWithStats(
            red_mask,
            connectivity=8,
        )
    )

    components = [
        tuple(
            int(value)
            for value in stats[index]
        )

        for index in range(1, count)

        if int(
            stats[index][cv2.CC_STAT_AREA]
        ) >= config.min_component_pixels
    ]

    return (
        page_width,
        page_height,
        red_mask,
        components,
    )


def detect_red_mark_pdf(
    pdf_path: str | Path,
    question_number: int,
    page_number: int | None,
    option_labels: list[str],
    page_ocr: dict[str, Any] | None = None,
    config: RedMarkConfig | None = None,
) -> dict[str, Any]:

    config = (
        config
        or RedMarkConfig.from_environment()
    )

    base = {
        "detected_answer": None,

        "confidence": 0.0,

        "status": "not_found",

        "page_number": page_number or 1,

        "mark_coordinates": None,

        "config_fingerprint": config.fingerprint,
    }

    with fitz.open(pdf_path) as document:

        page_index = (
            page_number
            if page_number is not None
            else 1
        ) - 1

        if not 0 <= page_index < len(document):
            return base

        page = document[page_index]

        located = locate(
            page,
            question_number,
            page_ocr,
        )

        if not located:
            return base

        available = {
            label: located["options"][label]

            for label in option_labels

            if label in located["options"]
        }

        if not available:
            return base

        stat = Path(pdf_path).stat()

        (
            page_width,
            page_height,
            red_mask,
            components,
        ) = _cached_page_red_data(
            str(Path(pdf_path).resolve()),
            stat.st_mtime_ns,
            stat.st_size,
            page_index,
            config,
        )

        scale = config.dpi / 72

        candidate_scores: dict[
            str,
            tuple[
                float,
                tuple[int, int, int, int] | None,
            ],
        ] = {}

        for label, option_box in available.items():

            local_px = tuple(
                value * scale
                for value in option_box
            )

            search = _expanded(
                local_px,
                config.max_distance_points * scale,
                red_mask.shape[1],
                red_mask.shape[0],
            )

            rx0, ry0, rx1, ry1 = search

            local_count = (
                int(
                    np.count_nonzero(
                        red_mask[
                            ry0:ry1,
                            rx0:rx1,
                        ]
                    )
                )

                if rx1 > rx0 and ry1 > ry0

                else 0
            )

            best_component_score = 0.0

            best_component_box = None

            for (
                x,
                y,
                width,
                height,
                area,
            ) in components:

                component_box = (
                    x,
                    y,
                    x + width,
                    y + height,
                )

                distance_points = (
                    _rect_distance(
                        component_box,
                        local_px,
                    ) / scale
                )

                if (
                    distance_points
                    > config.max_distance_points
                ):
                    continue

                area_score = min(
                    1.0,
                    area / config.min_red_pixels,
                )

                proximity_score = math.exp(
                    -distance_points
                    / max(
                        config.max_distance_points,
                        1,
                    )
                )

                score = (
                    area_score
                    * proximity_score
                )

                if score > best_component_score:

                    best_component_score = score

                    best_component_box = component_box

            density_score = min(
                1.0,
                local_count / max(
                    config.min_red_pixels,
                    1,
                ),
            )

            candidate_scores[label] = (
                0.75 * best_component_score
                + 0.25 * density_score,
                best_component_box,
            )

        ranked = sorted(
            candidate_scores.items(),

            key=lambda item: item[1][0],

            reverse=True,
        )

        if (
            not ranked

            or ranked[0][1][0]
            < config.medium_confidence
        ):
            return base

        best_label, (
            best_score,
            component_box,
        ) = ranked[0]

        second_score = (
            ranked[1][1][0]

            if len(ranked) > 1

            else 0.0
        )

        score_margin = (
            best_score - second_score
        )

        if (
            best_score >= config.high_confidence

            and score_margin >= config.min_score_margin

            and len(available) == len(
                set(option_labels)
            )
        ):
            status = "detected"

        else:
            status = "uncertain"

        mark_coordinates = None

        if component_box:
            mark_coordinates = [
                round(component_box[0] / scale, 2),
                round(component_box[1] / scale, 2),
                round(component_box[2] / scale, 2),
                round(component_box[3] / scale, 2),
            ]

        return {
            "detected_answer": best_label,

            "confidence": round(
                min(1.0, best_score),
                3,
            ),

            "status": status,

            "page_number": page_index + 1,

            "mark_coordinates": mark_coordinates,

            "config_fingerprint": config.fingerprint,
        }