
import unittest
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pymupdf

from backend.engine.glyph_geometry import acquire_pdf_glyphs
from backend.engine.ocr_job_journal import OCRJobJournal
from backend.inspect_glyph_cache import geometry_inventory, inspect_cached_job
from backend.engine.text_span_mapping import map_underline_spans
from backend.engine.visual_primitives import HorizontalStroke, detect_horizontal_strokes


def pdf_fixture(mode="text"):
    with pymupdf.open() as doc:
        page = doc.new_page(width=320, height=160)

        if mode == "scan":
            pix = pymupdf.Pixmap(
                pymupdf.csGRAY,
                pymupdf.IRect(0, 0, 40, 20),
                False,
            )
            pix.clear_with(200)
            page.insert_image(
                pymupdf.Rect(20, 20, 100, 60),
                pixmap=pix,
            )
        else:
            page.insert_text(
                (20, 40),
                "ilW",
                fontsize=20,
                render_mode=3 if mode == "hidden" else 0,
            )

        if mode == "rotated":
            page.set_rotation(90)

        if mode == "vertical":
            page.insert_text(
                (200, 100),
                "AB",
                fontsize=16,
                rotate=90,
            )

        if mode == "crop":
            page.set_cropbox(
                pymupdf.Rect(10, 10, 300, 150)
            )

        if mode == "mixed":
            pix = pymupdf.Pixmap(
                pymupdf.csGRAY,
                pymupdf.IRect(0, 0, 40, 20),
                False,
            )
            pix.clear_with(200)
            page.insert_image(
                pymupdf.Rect(150, 70, 230, 110),
                pixmap=pix,
            )

        return doc.tobytes()


class GlyphGeometryTests(unittest.TestCase):

    def test_real_nonuniform_glyph_boxes(self):
        result = acquire_pdf_glyphs(
            pdf_fixture(), 1, scale=3
        )

        self.assertTrue(result.usable_for_mapping)
        self.assertEqual(result.gray.shape, (480, 960))
        self.assertFalse(result.gray.flags.writeable)

        mapping, = result.mappings

        self.assertEqual(mapping.text, "ilW")

        widths = [
            g.bbox[2] - g.bbox[0]
            for g in mapping.glyphs
        ]

        self.assertGreater(widths[2], 3 * widths[0])

    def test_actual_pdf_geometry_to_span(self):
        result = acquire_pdf_glyphs(
            pdf_fixture(), 1, scale=3
        )

        region, = result.regions
        glyph = result.mappings[0].glyphs[2]

        x0 = round(glyph.bbox[0])
        x1 = round(glyph.bbox[2])
        y0 = round(region.bbox[3]) + 1

        stroke = HorizontalStroke(
            x0, y0, x1, y0 + 2, (x1 - x0) * 2
        )

        mapped, = map_underline_spans(
            [stroke],
            result.regions,
            result.mappings,
            stroke_frame=result.frame,
            text_frame=result.frame,
            glyph_frame=result.frame,
        )

        self.assertEqual(mapped.span.text, "W")

    def test_rendered_vector_underline(self):
        with pymupdf.open() as doc:
            page = doc.new_page(
                width=320,
                height=160,
            )

            page.insert_text(
                (20, 40),
                "ilW",
                fontsize=20,
            )

            page.draw_line(
                (28.88, 48),
                (47.76, 48),
                width=1,
            )

            source = doc.tobytes()

        result = acquire_pdf_glyphs(
            source, 1, scale=3
        )

        strokes = detect_horizontal_strokes(
            result.gray,
            reference_height=60,
        )

        mapped = map_underline_spans(
            strokes,
            result.regions,
            result.mappings,
            stroke_frame=result.frame,
            text_frame=result.frame,
            glyph_frame=result.frame,
        )

        self.assertEqual(
            [
                m.span.text
                for m in mapped
                if m.span is not None
            ],
            ["W"],
        )

    def test_frame_identity_and_scale(self):
        source = pdf_fixture()

        a = acquire_pdf_glyphs(source, 1, scale=2)
        b = acquire_pdf_glyphs(source, 1, scale=2)
        c = acquire_pdf_glyphs(source, 1, scale=3)

        self.assertEqual(a.frame, b.frame)
        self.assertNotEqual(a.frame, c.frame)

        self.assertAlmostEqual(
            a.mappings[0].glyphs[0].bbox[0] / 2,
            c.mappings[0].glyphs[0].bbox[0] / 3,
        )

    def test_scan_and_mixed_do_not_invent_glyphs(self):
        for mode in ("scan", "mixed"):
            result = acquire_pdf_glyphs(
                pdf_fixture(mode), 1
            )

            self.assertTrue(result.raster_images)
            self.assertFalse(result.usable_for_mapping)
            self.assertEqual(result.mappings, ())

    def test_hidden_text_not_mapped(self):
        result = acquire_pdf_glyphs(
            pdf_fixture("hidden"), 1
        )

        self.assertTrue(result.invisible_text)
        self.assertFalse(result.usable_for_mapping)
        self.assertEqual(result.mappings, ())

    def test_unmapped_neighbor_is_retained(self):
        result = acquire_pdf_glyphs(
            pdf_fixture("vertical"), 1
        )

        self.assertEqual(len(result.regions), 2)
        self.assertEqual(len(result.mappings), 1)
        self.assertEqual(len(result.unmapped), 1)

    def test_cropbox_origin(self):
        result = acquire_pdf_glyphs(
            pdf_fixture("crop"), 1, scale=2
        )

        self.assertEqual(
            result.gray.shape,
            (280, 580),
        )

        self.assertAlmostEqual(
            result.mappings[0].glyphs[0].bbox[0],
            20,
        )

    def test_reject_rotated_or_bad_inputs(self):
        with self.assertRaisesRegex(
            ValueError,
            "Rotated",
        ):
            acquire_pdf_glyphs(
                pdf_fixture("rotated"), 1
            )

        for page, scale in (
            (0, 3),
            (2, 3),
            (True, 3),
            (1, 0),
            (1, float("nan")),
            (1, 5),
        ):
            with self.assertRaises(ValueError):
                acquire_pdf_glyphs(
                    pdf_fixture(),
                    page,
                    scale=scale,
                )


class GlyphCacheInspectionTests(unittest.TestCase):

    def test_field_inventory_does_not_expose_values(self):
        data = {
            "pruned_result": {
                "rec_texts": ["PRIVATE TEXT"],
                "rec_boxes": [[1, 2, 3, 4]],
                "word_boxes": [[[1, 2, 3, 4]]],
                "model_settings": {
                    "use_doc_unwarping": True
                },
            }
        }

        result = geometry_inventory(data)

        fields = {
            f["path"]: f["shapes"]
            for f in result["fields"]
        }

        self.assertEqual(
            fields["page.pruned_result.rec_boxes"],
            ["list[1]/list[4]/int"],
        )

        self.assertIn(
            "page.pruned_result.word_boxes",
            fields,
        )

        self.assertNotIn(
            "PRIVATE TEXT",
            str(result),
        )

        self.assertFalse(result["truncated"])

    def test_readonly_cache_identity_and_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.sqlite3"
            scope = "a" * 64

            journal = OCRJobJournal(
                scope,
                path,
            )

            key = journal.batch_key(
                0, 1, "test"
            )

            claim = journal.reserve(
                key
            ).record.claim_id

            journal.accept(
                key,
                claim,
                "job-1",
            )

            journal.finish_with_result(
                key,
                claim,
                "job-1",
                [
                    {
                        "rec_boxes": [
                            [1, 2, 3, 4]
                        ]
                    }
                ],
                {},
                page_count=1,
            )

            before = path.read_bytes()

            self.assertEqual(
                inspect_cached_job(
                    path,
                    scope,
                    "job-1",
                )["job_id"],
                "job-1",
            )

            self.assertEqual(
                path.read_bytes(),
                before,
            )

            with self.assertRaises(RuntimeError):
                inspect_cached_job(
                    path,
                    "b" * 64,
                    "job-1",
                )

            # Commit the transaction and explicitly close the
            # SQLite connection before temporary directory cleanup.
            # Required for reliable execution on Windows.
            with closing(sqlite3.connect(path)) as conn:
                with conn:
                    conn.execute(
                        "UPDATE ocr_results SET sha256='bad'"
                    )

            with self.assertRaisesRegex(
                RuntimeError,
                "checksum",
            ):
                inspect_cached_job(
                    path,
                    scope,
                    "job-1",
                )


if __name__ == "__main__":
    unittest.main()
