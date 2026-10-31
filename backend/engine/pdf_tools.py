"""PDF question crops and conservative red-ink answer-mark detection."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np


QUESTION_RE = re.compile(r"^\s*(?:câu|cau|question|q)\s*(\d+)\s*[:.)-]?", re.I)
ORDINAL_RE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s+\S", re.I)
OPTION_RE = re.compile(r"^\s*(?:\(([A-H])\)|([A-H])\s*[.):])", re.I)


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
            dpi=int(os.getenv("PDF_RENDER_DPI", "200")),
            hue_low_max=int(os.getenv("RED_HUE_LOW_MAX", "10")),
            hue_high_min=int(os.getenv("RED_HUE_HIGH_MIN", "170")),
            saturation_min=int(os.getenv("RED_SATURATION_MIN", "75")),
            value_min=int(os.getenv("RED_VALUE_MIN", "45")),
            morphology_kernel=max(1, int(os.getenv("RED_MORPHOLOGY_KERNEL", "3"))),
            min_component_pixels=int(os.getenv("RED_MIN_COMPONENT_PIXELS", "8")),
            min_red_pixels=int(os.getenv("RED_MIN_PIXELS", "24")),
            max_distance_points=float(os.getenv("RED_MAX_DISTANCE_POINTS", "32")),
            crop_padding_points=float(os.getenv("PDF_CROP_PADDING_POINTS", "14")),
            high_confidence=float(os.getenv("RED_HIGH_CONFIDENCE", "0.76")),
            medium_confidence=float(os.getenv("RED_MEDIUM_CONFIDENCE", "0.42")),
            min_score_margin=float(os.getenv("RED_MIN_SCORE_MARGIN", "0.14")),
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


def _question_number(text: str) -> int | None:
    match = QUESTION_RE.match(text)
    if match:
        return int(match.group(1))
    match = ORDINAL_RE.match(text)
    return int(match.group(1)) if match else None


def _lines_from_words(words: list[tuple]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    for word in words:
        if len(word) >= 8:
            grouped[(int(word[5]), int(word[6]))].append(word)
    lines = []
    for parts in grouped.values():
        parts.sort(key=lambda item: item[0])
        lines.append({
            "text": " ".join(str(part[4]) for part in parts),
            "bbox": (min(part[0] for part in parts), min(part[1] for part in parts),
                     max(part[2] for part in parts), max(part[3] for part in parts)),
        })
    return sorted(lines, key=lambda line: (line["bbox"][1], line["bbox"][0]))


def _find_column(page: fitz.Page, lines: list[dict[str, Any]], x_center: float) -> tuple[float, float]:
    width = page.rect.width
    intervals = sorted((line["bbox"][0], line["bbox"][2]) for line in lines)
    candidates = []
    previous_right = None
    for left, right in intervals:
        if previous_right is not None:
            gap = left - previous_right
            middle = (left + previous_right) / 2
            if gap >= width * 0.055 and width * 0.2 < middle < width * 0.8:
                left_count = sum(1 for a, _ in intervals if a < middle)
                right_count = sum(1 for _, b in intervals if b > middle)
                if left_count >= 4 and right_count >= 4:
                    candidates.append((gap, middle))
        previous_right = max(previous_right or right, right)

    if not candidates:
        return 0.0, width
    _, divider = max(candidates)
    return (0.0, divider) if x_center < divider else (divider, width)


def _ocr_boxes(page_ocr: dict[str, Any] | None, page: fitz.Page) -> list[dict[str, Any]]:
    if not isinstance(page_ocr, dict):
        return []
    boxes: list[tuple[str, tuple[float, float, float, float]]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            texts = node.get("rec_texts")
            rects = node.get("rec_boxes") or node.get("dt_boxes")
            if isinstance(texts, list) and isinstance(rects, list) and len(texts) == len(rects):
                for text, rect in zip(texts, rects):
                    parsed = _bbox(rect)
                    if parsed and str(text).strip():
                        boxes.append((str(text).strip(), parsed))
            for key in ("block_content", "text", "rec_text"):
                text = node.get(key)
                rect = node.get("block_bbox") or node.get("bbox") or node.get("box")
                parsed = _bbox(rect)
                if isinstance(text, str) and text.strip() and parsed:
                    boxes.append((text.strip(), parsed))
            for key, child in node.items():
                if key not in ("rec_texts", "rec_boxes", "dt_boxes"):
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(page_ocr)
    if not boxes:
        return []
    max_x = max(box[2] for _, box in boxes)
    max_y = max(box[3] for _, box in boxes)
    sx = page.rect.width / max_x if max_x > page.rect.width * 1.2 else (page.rect.width if max_x <= 1.2 else 1.0)
    sy = page.rect.height / max_y if max_y > page.rect.height * 1.2 else (page.rect.height if max_y <= 1.2 else 1.0)
    normalized = [(text, (box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy)) for text, box in boxes]
    normalized.sort(key=lambda item: ((item[1][1] + item[1][3]) / 2, item[1][0]))
    lines: list[dict[str, Any]] = []
    for text, rect in normalized:
        center_y = (rect[1] + rect[3]) / 2
        target = next((line for line in lines if abs(line["center_y"] - center_y) <= max(4.0, (rect[3] - rect[1]) * 0.6)), None)
        if target is None:
            lines.append({"text": text, "bbox": rect, "center_y": center_y})
        else:
            target.setdefault("parts", [(target["bbox"][0], target["text"])])
            target["parts"].append((rect[0], text))
            target["bbox"] = (min(target["bbox"][0], rect[0]), min(target["bbox"][1], rect[1]), max(target["bbox"][2], rect[2]), max(target["bbox"][3], rect[3]))
    for line in lines:
        if "parts" in line:
            line["text"] = " ".join(text for _, text in sorted(line["parts"]))
        line.pop("center_y", None)
        line.pop("parts", None)
    return sorted(lines, key=lambda line: (line["bbox"][1], line["bbox"][0]))


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if len(numbers) == 4:
        x0, y0, x1, y1 = numbers
    elif len(numbers) >= 8:
        xs, ys = numbers[0::2], numbers[1::2]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    else:
        return None
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _locate_from_lines(page: fitz.Page, question_number: int, lines: list[dict[str, Any]], source: str) -> dict[str, Any] | None:
    if not lines:
        return None
    starts = [(index, _question_number(line["text"])) for index, line in enumerate(lines)]
    target = next((index for index, number in starts if number == question_number), None)
    if target is None:
        return None

    heading = lines[target]
    bbox = heading["bbox"]
    x0, x1 = _find_column(page, lines, (bbox[0] + bbox[2]) / 2)
    next_start = None
    for index, number in starts:
        if index <= target or number is None:
            continue
        other = lines[index]["bbox"]
        center = (other[0] + other[2]) / 2
        if x0 <= center <= x1:
            next_start = index
            break

    end_y = lines[next_start]["bbox"][1] if next_start is not None else page.rect.height
    options: dict[str, tuple[float, float, float, float]] = {}
    for line in lines[target + 1 : next_start]:
        line_box = line["bbox"]
        center = (line_box[0] + line_box[2]) / 2
        if not (x0 <= center <= x1):
            continue
        option_match = OPTION_RE.match(line["text"])
        if option_match:
            label = (option_match.group(1) or option_match.group(2)).upper()
            options[label] = line_box

    return {
        "bbox": (x0, max(0.0, bbox[1]), x1, max(bbox[1] + 1, end_y)),
        "question_start_y": bbox[1],
        "next_start_y": end_y if next_start is not None else None,
        "column_x": (x0, x1),
        "options": options,
        "source": source,
    }


def _locate(page: fitz.Page, question_number: int, page_ocr: dict[str, Any] | None = None) -> dict[str, Any] | None:
    pdf_lines = _lines_from_words(page.get_text("words"))
    located = _locate_from_lines(page, question_number, pdf_lines, "pymupdf")
    if located:
        return located
    return _locate_from_lines(page, question_number, _ocr_boxes(page_ocr, page), "paddleocr")


def _render_segment(page: fitz.Page, clip: tuple[float, float, float, float], dpi: int) -> np.ndarray:
    rect = fitz.Rect(*clip) & page.rect
    if rect.is_empty or rect.width <= 0 or rect.height <= 0:
        raise ValueError("Invalid PDF crop rectangle")
    pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=rect, alpha=False, annots=True)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)


def _question_segments(document: fitz.Document, question_number: int, first_page: int, padding: float, page_ocr: list[dict[str, Any]] | None = None) -> tuple[list[tuple[int, tuple[float, float, float, float]]], dict[str, Any] | None]:
    if first_page < 0 or first_page >= len(document):
        first_page = 0
    page = document[first_page]
    page_ocr = page_ocr or []
    located = _locate(page, question_number, page_ocr[first_page] if first_page < len(page_ocr) else None)
    if located is None:
        return [(first_page, (0, 0, page.rect.width, page.rect.height))], None

    x0, x1 = located["column_x"]
    start_y = max(0.0, located["question_start_y"] - padding)
    next_y = located["next_start_y"]
    if next_y is not None:
        return [(first_page, (x0, start_y, x1, max(start_y + 1, next_y - padding / 2)))], located

    segments = [(first_page, (x0, start_y, x1, page.rect.height))]
    for page_index in range(first_page + 1, len(document)):
        next_page = document[page_index]
        next_located = _locate(next_page, question_number + 1, page_ocr[page_index] if page_index < len(page_ocr) else None)
        if next_located:
            next_x0, next_x1 = _find_column(next_page, _lines_from_words(next_page.get_text("words")), (x0 + x1) / 2)
            stop_y = next_located["question_start_y"] - padding / 2
            segments.append((page_index, (next_x0, 0, next_x1, max(1.0, stop_y))))
            break
        segments.append((page_index, (0, 0, next_page.rect.width, next_page.rect.height)))
        if len(segments) >= 3:
            break
    return segments, located


def _join_images(images: list[np.ndarray]) -> np.ndarray:
    max_width = max(image.shape[1] for image in images)
    normalized = []
    for image in images:
        if image.shape[1] < max_width:
            image = cv2.copyMakeBorder(image, 0, 0, 0, max_width - image.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255))
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
    config = config or RedMarkConfig.from_environment()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as document:
        first_page = max(0, (page_number or 1) - 1)
        segments, located = _question_segments(document, question_number, first_page, config.crop_padding_points, ocr_pages)
        images = [_render_segment(document[index], clip, config.dpi) for index, clip in segments]
        image = _join_images(images)
        if not cv2.imwrite(str(output_path), image):
            raise OSError("Could not write cropped question image")
        coordinates = [
            {"page_number": index + 1, "bbox": [round(value, 2) for value in clip]}
            for index, clip in segments
        ]
        return {
            "image_path": str(output_path),
            "page_number": first_page + 1,
            "crop_coordinates": coordinates,
            "crop_status": "success",
            "source": located["source"] if located else "full_page_fallback",
        }


def _expanded(box: tuple[float, float, float, float], margin: float, width: int, height: int) -> tuple[int, int, int, int]:
    return (
        max(0, int(box[0] - margin)), max(0, int(box[1] - margin)),
        min(width, int(box[2] + margin)), min(height, int(box[3] + margin)),
    )


def _rect_distance(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
    dx = max(first[0] - second[2], second[0] - first[2], 0)
    dy = max(first[1] - second[3], second[1] - first[3], 0)
    return math.hypot(dx, dy)


@lru_cache(maxsize=2)
def _cached_page_red_data(
    pdf_path: str,
    modified_ns: int,
    file_size: int,
    page_index: int,
    config: RedMarkConfig,
) -> tuple[float, float, np.ndarray, list[tuple[int, int, int, int, int]]]:
    with fitz.open(pdf_path) as document:
        page = document[page_index]
        page_width, page_height = page.rect.width, page.rect.height
        image = _render_segment(page, (0, 0, page_width, page_height), config.dpi)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lower = cv2.inRange(hsv, np.array([0, config.saturation_min, config.value_min]), np.array([config.hue_low_max, 255, 255]))
    upper = cv2.inRange(hsv, np.array([config.hue_high_min, config.saturation_min, config.value_min]), np.array([179, 255, 255]))
    red_mask = cv2.bitwise_or(lower, upper)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (config.morphology_kernel, config.morphology_kernel))
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, kernel)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(red_mask, connectivity=8)
    components = [
        tuple(int(value) for value in stats[index])
        for index in range(1, count)
        if int(stats[index][cv2.CC_STAT_AREA]) >= config.min_component_pixels
    ]
    return page_width, page_height, red_mask, components


def detect_red_mark_pdf(
    pdf_path: str | Path,
    question_number: int,
    page_number: int | None,
    option_labels: list[str],
    page_ocr: dict[str, Any] | None = None,
    config: RedMarkConfig | None = None,
) -> dict[str, Any]:
    config = config or RedMarkConfig.from_environment()
    base = {
        "detected_answer": None,
        "confidence": 0.0,
        "status": "not_found",
        "page_number": page_number or 1,
        "mark_coordinates": None,
        "config_fingerprint": config.fingerprint,
    }
    with fitz.open(pdf_path) as document:
        page_index = max(0, min((page_number or 1) - 1, len(document) - 1))
        page = document[page_index]
        located = _locate(page, question_number, page_ocr)
        if not located:
            return base
        available = {label: located["options"][label] for label in option_labels if label in located["options"]}
        if not available:
            return base

        qx0, qy0, _, _ = located["bbox"]
        stat = Path(pdf_path).stat()
        page_width, page_height, red_mask, components = _cached_page_red_data(
            str(Path(pdf_path).resolve()), stat.st_mtime_ns, stat.st_size, page_index, config,
        )
        scale = config.dpi / 72
        candidate_scores: dict[str, tuple[float, tuple[int, int, int, int] | None]] = {}
        for label, option_box in available.items():
            local = option_box
            local_px = tuple(value * scale for value in local)
            search = _expanded(local_px, config.max_distance_points * scale, red_mask.shape[1], red_mask.shape[0])
            rx0, ry0, rx1, ry1 = search
            local_count = int(np.count_nonzero(red_mask[ry0:ry1, rx0:rx1])) if rx1 > rx0 and ry1 > ry0 else 0
            best_component_score = 0.0
            best_component_box = None
            for x, y, width, height, area in components:
                component_box = (x, y, x + width, y + height)
                distance_points = _rect_distance(component_box, local_px) / scale
                if distance_points > config.max_distance_points:
                    continue
                area_score = min(1.0, area / config.min_red_pixels)
                proximity_score = math.exp(-distance_points / max(config.max_distance_points, 1))
                score = area_score * proximity_score
                if score > best_component_score:
                    best_component_score = score
                    best_component_box = component_box
            density_score = min(1.0, local_count / max(config.min_red_pixels, 1))
            candidate_scores[label] = (0.75 * best_component_score + 0.25 * density_score, best_component_box)

        ranked = sorted(candidate_scores.items(), key=lambda item: item[1][0], reverse=True)
        if not ranked or ranked[0][1][0] < config.medium_confidence:
            return base
        best_label, (best_score, component_box) = ranked[0]
        second_score = ranked[1][1][0] if len(ranked) > 1 else 0.0
        score_margin = best_score - second_score
        if best_score >= config.high_confidence and score_margin >= config.min_score_margin:
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
            "confidence": round(min(1.0, best_score), 3),
            "status": status,
            "page_number": page_index + 1,
            "mark_coordinates": mark_coordinates,
            "config_fingerprint": config.fingerprint,
        }
