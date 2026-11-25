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
OPTION_RE = re.compile(r"(?<![A-Za-z0-9])(?:\(([A-D])\)|([A-D])\s*[.,:)])", re.ASCII)
SECTION_ONE_RE = re.compile(r"^\s*(?:#{1,6}\s*)?QUESTION\s+I\b", re.I)
SECTION_TITLE_RE = re.compile(r"^\s*(?:#{1,6}\s*)?QUESTION\s+[IVXLCDM]+\b", re.I)


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
    high_confidence: float = 0.72
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
            high_confidence=float(os.getenv("RED_HIGH_CONFIDENCE", "0.72")),
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
            "parts": [
                {"text": str(part[4]), "bbox": (part[0], part[1], part[2], part[3])}
                for part in parts
            ],
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
    dimensions: list[tuple[float, float]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            width = node.get("width") or node.get("image_width") or node.get("img_width")
            height = node.get("height") or node.get("image_height") or node.get("img_height")
            try:
                if float(width) > 0 and float(height) > 0:
                    dimensions.append((float(width), float(height)))
            except (TypeError, ValueError):
                pass
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
                    text_lines = [line.strip() for line in text.splitlines() if line.strip()]
                    if len(text_lines) > 1:
                        line_height = (parsed[3] - parsed[1]) / len(text_lines)
                        for index, text_line in enumerate(text_lines):
                            top = parsed[1] + index * line_height
                            bottom = parsed[1] + (index + 1) * line_height
                            boxes.append((text_line, (parsed[0], top, parsed[2], bottom)))
                    else:
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
    # Prefer explicit OCR canvas dimensions. Inferring scale from the largest
    # detected box fails when content does not reach the page's right/bottom edge.
    dimension = next(((width, height) for width, height in dimensions
                      if width >= max_x and height >= max_y), None)
    if dimension:
        sx, sy = page.rect.width / dimension[0], page.rect.height / dimension[1]
    else:
        sx = page.rect.width / max_x if max_x > page.rect.width * 1.2 else (page.rect.width if max_x <= 1.2 else 1.0)
        sy = page.rect.height / max_y if max_y > page.rect.height * 1.2 else (page.rect.height if max_y <= 1.2 else 1.0)
    normalized = [(text, (box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy)) for text, box in boxes]
    unique = {}
    for text, rect in normalized:
        key = (text, tuple(round(value, 2) for value in rect))
        unique[key] = (text, rect)
    normalized = list(unique.values())
    normalized.sort(key=lambda item: ((item[1][1] + item[1][3]) / 2, item[1][0]))
    lines: list[dict[str, Any]] = []
    for text, rect in normalized:
        center_y = (rect[1] + rect[3]) / 2
        target = next((line for line in lines if abs(line["center_y"] - center_y) <= max(4.0, (rect[3] - rect[1]) * 0.6)), None)
        if target is None:
            lines.append({"text": text, "bbox": rect, "center_y": center_y,
                          "parts": [{"text": text, "bbox": rect}]})
        else:
            target["parts"].append({"text": text, "bbox": rect})
            target["bbox"] = (min(target["bbox"][0], rect[0]), min(target["bbox"][1], rect[1]), max(target["bbox"][2], rect[2]), max(target["bbox"][3], rect[3]))
    for line in lines:
        line["parts"].sort(key=lambda part: part["bbox"][0])
        line["text"] = " ".join(part["text"] for part in line["parts"])
        line.pop("center_y", None)
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


def _option_boxes_for_line(line: dict[str, Any]) -> dict[str, tuple[float, float, float, float]]:
    """Estimate each option's own horizontal extent from OCR/PDF word positions."""
    parts = line.get("parts") or [{"text": line["text"], "bbox": line["bbox"]}]
    starts: list[tuple[float, str]] = []
    for part in parts:
        text = str(part["text"])
        box = part["bbox"]
        for match in OPTION_RE.finditer(text):
            label = (match.group(1) or match.group(2)).upper()
            # OCR may return one block for an entire horizontal row. Interpolate the
            # marker position within that block; individual PDF/OCR words use exact boxes.
            ratio = match.start() / max(len(text), 1)
            marker_x = box[0] + (box[2] - box[0]) * ratio
            starts.append((marker_x, label))
    starts.sort(key=lambda item: item[0])
    result = {}
    for index, (left, label) in enumerate(starts):
        right = starts[index + 1][0] if index + 1 < len(starts) else line["bbox"][2]
        right = max(left + 1.0, right)
        result[label] = (left, line["bbox"][1], right, line["bbox"][3])
    return result


def _locate_from_lines(page: fitz.Page, question_number: int, lines: list[dict[str, Any]], source: str) -> dict[str, Any] | None:
    if not lines:
        return None
    starts = [(index, _question_number(line["text"])) for index, line in enumerate(lines)]
    target = next((index for index, number in starts if number == question_number), None)
    section_fallback = False
    if target is None and question_number == 1:
        target = next((index for index, line in enumerate(lines) if SECTION_ONE_RE.match(line["text"])), None)
        section_fallback = target is not None
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

    # QUESTION I may describe a section rather than an individual OCR question
    # record. Its lower edge is then the next numbered question, not the heading
    # box itself (which can span the instruction and first answer row).
    if section_fallback and next_start is None:
        return None
    end_y = lines[next_start]["bbox"][1] if next_start is not None else page.rect.height
    options: dict[str, tuple[float, float, float, float]] = {}
    offset_options: set[str] = set()
    for line in ([] if section_fallback else lines[target : next_start]):
        if SECTION_TITLE_RE.match(line["text"]):
            continue
        line_box = line["bbox"]
        center = (line_box[0] + line_box[2]) / 2
        if not (x0 <= center <= x1 or OPTION_RE.search(line["text"])):
            continue
        line_options = _option_boxes_for_line(line)
        if source == "paddleocr" and len(line.get("parts", [])) == 1 and len(OPTION_RE.findall(line["text"])) > 1:
            offset_options.update(line_options)
        for label, option_box in line_options.items():
            options[label] = option_box
            x0 = min(x0, option_box[0])
            x1 = max(x1, option_box[2])

    if section_fallback:
        x0, x1 = 0.0, page.rect.width
    return {
        "bbox": (x0, max(0.0, bbox[1]), x1, max(bbox[1] + 1, end_y)),
        "question_start_y": bbox[1],
        "next_start_y": end_y if next_start is not None else None,
        "column_x": (x0, x1),
        "options": options,
        "offset_options": offset_options,
        "source": source,
        "section_fallback": section_fallback,
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
        stop_y = next_y - max(padding, 16.0) if located.get("section_fallback") else next_y - padding / 2
        return [(first_page, (x0, start_y, x1, max(start_y + 1, stop_y)))], located

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
            "crop_status": "success" if located else "review_required",
            "source": located["source"] if located else "full_page_fallback",
            "review_required": located is None,
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
) -> tuple[float, float, np.ndarray, list[tuple[int, int, int, int, float, float, float, float]]]:
    with fitz.open(pdf_path) as document:
        page = document[page_index]
        page_width, page_height = page.rect.width, page.rect.height
        image = _render_segment(page, (0, 0, page_width, page_height), config.dpi)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lower = cv2.inRange(hsv, np.array([0, config.saturation_min, config.value_min]), np.array([config.hue_low_max, 255, 255]))
    upper = cv2.inRange(hsv, np.array([config.hue_high_min, config.saturation_min, config.value_min]), np.array([179, 255, 255]))
    red_mask = cv2.bitwise_or(lower, upper)
    close_size = max(7, config.morphology_kernel * 4)
    if close_size % 2 == 0:
        close_size += 1
    close_size = min(close_size, 11)
    close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size))
    clean_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (config.morphology_kernel, config.morphology_kernel))
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, close_kernel)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, clean_kernel)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, close_kernel)

    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    components = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = float(cv2.contourArea(contour))
        perimeter = float(cv2.arcLength(contour, True))
        if area < config.min_component_pixels or min(width, height) < 10 or perimeter <= 0:
            continue
        circularity = 4 * math.pi * area / (perimeter * perimeter)
        aspect_ratio = width / max(height, 1)
        density = float(np.count_nonzero(red_mask[y:y + height, x:x + width])) / max(width * height, 1)
        coverage = 0.0
        if len(contour) >= 5:
            (center_x, center_y), _, _ = cv2.fitEllipse(contour)
            points = contour.reshape(-1, 2).astype(np.float32)
            angles = (np.degrees(np.arctan2(points[:, 1] - center_y, points[:, 0] - center_x)) + 360) % 360
            coverage = float(np.count_nonzero(np.histogram(angles, bins=36, range=(0, 360))[0])) / 36
        # Long strokes and solid red blobs are not selection circles.
        if (circularity < 0.30 and coverage < 0.68) or not 0.35 <= aspect_ratio <= 4.5 or density > 0.68:
            continue
        components.append((x, y, width, height, area, circularity, aspect_ratio, coverage))
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
        stat = Path(pdf_path).stat()
        _, _, red_mask, components = _cached_page_red_data(
            str(Path(pdf_path).resolve()), stat.st_mtime_ns, stat.st_size, page_index, config,
        )
        scale = config.dpi / 72
        if not available:
            # OCR can retain the section boundary while completely omitting a question's
            # options (as on the scanned Q1 reference). Report the visible circle for review,
            # but never assign it to an answer without option geometry.
            start_y = located["question_start_y"] - config.crop_padding_points
            end_y = located.get("next_start_y") or located["bbox"][3]
            unassigned = []
            for x, y, width, height, area, circularity, aspect_ratio, coverage in components:
                center_y = (y + height / 2) / scale
                if start_y <= center_y <= end_y + config.max_distance_points:
                    unassigned.append((area, x, y, width, height))
            if unassigned:
                _, x, y, width, height = max(unassigned)
                base.update({
                    "confidence": 0.5,
                    "status": "uncertain",
                    "page_number": page_index + 1,
                    "mark_coordinates": [round(x / scale, 2), round(y / scale, 2),
                                         round((x + width) / scale, 2), round((y + height) / scale, 2)],
                })
            return base

        candidate_scores: dict[str, tuple[float, tuple[int, int, int, int] | None]] = {}
        circle_near_choices = False
        for label, option_box in available.items():
            local_px = tuple(value * scale for value in option_box)
            best_component_score = 0.0
            best_component_box = None
            for x, y, width, height, _, circularity, aspect_ratio, coverage in components:
                component_box = (x, y, x + width, y + height)
                component_center_y = (component_box[1] + component_box[3]) / (2 * scale)
                if component_center_y < located["question_start_y"] - config.max_distance_points:
                    continue
                if (located["next_start_y"] is not None
                        and component_center_y >= located["next_start_y"] + config.max_distance_points):
                    continue
                distance_points = _rect_distance(component_box, local_px) / scale
                if distance_points > config.max_distance_points:
                    continue
                circle_near_choices = True
                intersection_width = max(0.0, min(component_box[2], local_px[2]) - max(component_box[0], local_px[0]))
                intersection_height = max(0.0, min(component_box[3], local_px[3]) - max(component_box[1], local_px[1]))
                circle_box_area = max((component_box[2] - component_box[0]) * (component_box[3] - component_box[1]), 1.0)
                option_box_area = max((local_px[2] - local_px[0]) * (local_px[3] - local_px[1]), 1.0)
                intersection_area = intersection_width * intersection_height
                # A circle around a small label fits inside the option box; one around the
                # whole answer can instead contain the option box. Score either relation.
                overlap_score = min(1.0, max(intersection_area / circle_box_area,
                                             intersection_area / option_box_area))
                center_x = (component_box[0] + component_box[2]) / 2
                center_y = (component_box[1] + component_box[3]) / 2
                label_tolerance = 2.0 * scale
                if label in located.get("offset_options", set()) and (component_box[2] - component_box[0]) / scale < 35:
                    # For OCR blocks containing a whole horizontal row, a small
                    # ring around the label should map to the nearest interpolated
                    # label anchor. Otherwise its bbox can straddle adjacent options.
                    anchors = {
                        choice: box[0] + min(6.0, (box[2] - box[0]) * 0.12)
                        for choice, box in available.items()
                    }
                    if len(anchors) > 1 and max(anchors.values()) - min(anchors.values()) > 20:
                        nearest = min(anchors, key=lambda choice: abs(center_x / scale - anchors[choice]))
                        if nearest != label:
                            continue
                center_inside = float(
                    local_px[0] - label_tolerance <= center_x <= local_px[2] + label_tolerance
                    and local_px[1] - label_tolerance <= center_y <= local_px[3] + label_tolerance
                )
                proximity_score = math.exp(-distance_points / max(config.max_distance_points, 1))
                label_center_x = local_px[0] + min(6.0 * scale, (local_px[2] - local_px[0]) * 0.12)
                label_distance = abs(center_x - label_center_x) / scale
                label_score = math.exp(-label_distance / 14.0)
                if located["source"] == "paddleocr" and label in located.get("offset_options", set()):
                    option_top = option_box[1]
                    option_height = max(option_box[3] - option_box[1], 1.0)
                    expected_mark_y = option_top - 0.9 * option_height
                else:
                    expected_mark_y = (option_box[1] + option_box[3]) / 2
                vertical_distance = abs(center_y / scale - expected_mark_y)
                vertical_score = math.exp(-vertical_distance / max(8.0, (option_box[3] - option_box[1]) * 0.8))
                if label in located.get("offset_options", set()):
                    # On the reference scan, consecutive inline rows are only ~16pt
                    # apart. A broad tolerance lets the previous question's circle
                    # overlap the next row's interpolated option box.
                    vertical_tolerance = max(8.0, (option_box[3] - option_box[1]) * 0.5)
                    if vertical_distance > vertical_tolerance:
                        continue
                aspect_score = max(0.35, math.exp(-abs(math.log(max(aspect_ratio, 1e-6))) / 0.9))
                shape_score = (0.40 * min(1.0, circularity / 0.65)
                               + 0.30 * aspect_score + 0.30 * coverage)
                cx0, cy0, cx1, cy1 = (int(value) for value in component_box)
                component_pixels = int(np.count_nonzero(red_mask[cy0:cy1, cx0:cx1]))
                if component_pixels < config.min_red_pixels:
                    continue
                component_density = component_pixels / circle_box_area
                density_score = min(1.0, component_density / 0.12)
                score = (0.36 * shape_score + 0.15 * overlap_score
                         + 0.08 * density_score + 0.04 * proximity_score
                         + 0.03 * center_inside + 0.16 * label_score
                         + 0.18 * vertical_score)
                # A hand-drawn ellipse around the whole answer can be wider than a
                # geometric circle. Strong overlap with one option is affirmative
                # evidence and compensates for that aspect-ratio penalty.
                if overlap_score >= 0.55:
                    score += 0.04
                if coverage >= 0.85:
                    # Near-complete angular coverage is strong evidence for a hand-drawn
                    # circle even when a small gap makes contour-area circularity low.
                    score += 0.18
                # Require meaningful overlap; a geometrically circular annotation merely
                # close to the answer text should not be assigned to that option.
                if overlap_score < 0.18 and center_inside == 0 and label_score < 0.25 and vertical_score < 0.25:
                    continue
                if score > best_component_score:
                    best_component_score = score
                    best_component_box = component_box
            candidate_scores[label] = (best_component_score, best_component_box)

        ranked = sorted(candidate_scores.items(), key=lambda item: item[1][0], reverse=True)
        if not ranked or ranked[0][1][0] < config.medium_confidence:
            if circle_near_choices:
                base["status"] = "uncertain"
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
            "detected_answer": best_label if status == "detected" else None,
            "confidence": round(min(1.0, best_score), 3),
            "status": status,
            "page_number": page_index + 1,
            "mark_coordinates": mark_coordinates,
            "config_fingerprint": config.fingerprint,
        }
