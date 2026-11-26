"""Acquire PDF text-layer glyph candidates in the exact full-page raster frame."""
from __future__ import annotations

import hashlib
import unicodedata as ud
from dataclasses import dataclass
import math

import numpy as np
import pymupdf

from .spatial_association import PixelFrame, TextRegion, _validate_box
from .text_span_mapping import GlyphSpan, RegionGlyphs, _validate_glyphs


@dataclass(frozen=True, slots=True)
class PdfGlyphPage:
    frame: PixelFrame
    gray: np.ndarray
    regions: tuple[TextRegion, ...]
    mappings: tuple[RegionGlyphs, ...]
    unmapped: tuple[tuple[str, str], ...]
    skipped_lines: int
    raster_images: bool
    invisible_text: bool

    @property
    def usable_for_mapping(self):
        return (bool(self.mappings) and not self.skipped_lines
                and not self.raster_images and not self.invisible_text)


def _pixel_box(box, matrix, pix, frame):
    rect = pymupdf.Rect(box) * matrix
    result = (float(rect.x0 - pix.x), float(rect.y0 - pix.y),
              float(rect.x1 - pix.x), float(rect.y1 - pix.y))
    _validate_box(result, frame)
    return result


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]),
            max(a[2], b[2]), max(a[3], b[3]))


def _line_mapping(line, region, matrix, pix, frame):
    if line.get("wmode") != 0 or tuple(line.get("dir", ())) != (1.0, 0.0):
        raise ValueError("Unsupported text direction.")
    text = ""
    glyphs = []
    for span in line.get("spans", []):
        for char in span.get("chars", []):
            value = char["c"]
            if not isinstance(value, str) or not value:
                raise ValueError("Invalid source character.")
            start, end = len(text), len(text) + len(value)
            if value.isspace():
                glyphs.append(GlyphSpan(start, end, None))
            else:
                if char.get("synthetic") is True or "\ufffd" in value:
                    raise ValueError("Unreliable source glyph.")
                box = _pixel_box(char["bbox"], matrix, pix, frame)
                if ud.category(value[0]).startswith("M"):
                    if not glyphs or glyphs[-1].bbox is None:
                        raise ValueError("Unattached combining mark.")
                    previous = glyphs.pop()
                    glyphs.append(GlyphSpan(previous.start, end, _union(previous.bbox, box)))
                else:
                    glyphs.append(GlyphSpan(start, end, box))
            text += value
    mapping = RegionGlyphs(region.region_id, text, tuple(glyphs))
    _validate_glyphs(mapping, region, frame)
    return mapping


def acquire_pdf_glyphs(pdf_bytes: bytes, page_number: int, *, scale: float = 3.0) -> PdfGlyphPage:
    """Read only. Unrotated full pages; font boxes are not verified ink boxes.

    Mixed/raster pages and invisible text are intentionally not mapped in v1.
    Retain valid unmapped text regions as competitors. If any region box is
    invalid, callers must not classify the partial set as complete.
    """
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes:
        raise ValueError("Expected PDF bytes.")
    if type(page_number) is not int or page_number < 1:
        raise ValueError("Invalid page number.")
    if type(scale) not in (int, float) or not math.isfinite(scale) or not 1 <= scale <= 4:
        raise ValueError("Invalid render scale.")
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        if not doc.is_pdf or doc.needs_pass or page_number > len(doc):
            raise ValueError("PDF page unavailable.")
        page = doc[page_number - 1]
        if page.rotation != 0:
            raise ValueError("Rotated pages are not supported yet.")
        matrix = pymupdf.Matrix(scale, scale)
        bounds = (page.rect * matrix).irect
        if bounds.width < 3 or bounds.height < 3 or bounds.width * bounds.height > 12_000_000:
            raise ValueError("Unsupported render dimensions.")
        pix = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY,
                              alpha=False, annots=False)
        if (pix.width, pix.height) != (bounds.width, bounds.height) or pix.n != 1:
            raise ValueError("Unexpected render frame.")
        samples = pix.samples
        gray = np.frombuffer(samples, dtype=np.uint8).reshape(pix.height, pix.width)
        digest = hashlib.sha256(pdf_bytes).hexdigest()
        render = hashlib.sha256(samples).hexdigest()
        frame = PixelFrame(f"pdf:{digest}:p{page_number}:s{float(scale).hex()}:{render}",
                           pix.width, pix.height)
        raster = bool(page.get_image_info())
        invisible = any(s.get("type") == 3 or s.get("opacity") == 0
                        for s in page.get_texttrace())
        regions, mappings, unmapped = [], [], []
        skipped = 0
        flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        for bi, block in enumerate(page.get_text("rawdict", flags=flags).get("blocks", [])):
            if block.get("type") != 0:
                continue
            for li, line in enumerate(block.get("lines", [])):
                key = f"p{page_number}:b{bi}:l{li}"
                try:
                    region = TextRegion(key, _pixel_box(line["bbox"], matrix, pix, frame))
                except (KeyError, TypeError, ValueError):
                    skipped += 1
                    continue
                regions.append(region)
                if raster or invisible:
                    unmapped.append((key, "mixed_raster_or_invisible_text"))
                    continue
                try:
                    mappings.append(_line_mapping(line, region, matrix, pix, frame))
                except (KeyError, TypeError, ValueError):
                    unmapped.append((key, "unsupported_glyph_geometry"))
        return PdfGlyphPage(frame, gray, tuple(regions), tuple(mappings),
                            tuple(unmapped), skipped, raster, invisible)
