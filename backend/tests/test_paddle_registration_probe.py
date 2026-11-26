import hashlib
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pymupdf

from backend.engine.ocr_job_journal import OCRJobJournal
from backend.engine.ocr_image_registration import RegistrationError
from backend.paddle_registration_probe import inspect, probe_points
from backend.tests.test_paddle_word_geometry import fixture


class RegistrationProbeTests(unittest.TestCase):

    def setUp(self):

        self.temp = tempfile.TemporaryDirectory()

        self.addCleanup(
            self.temp.cleanup
        )

        folder = Path(
            self.temp.name
        )

        self.pdf = folder / "sample.pdf"

        self.path = folder / "jobs.sqlite3"

        with pymupdf.open() as doc:

            doc.new_page(
                width=200,
                height=200,
            )

            doc.save(self.pdf)

        self.sha = hashlib.sha256(
            self.pdf.read_bytes()
        ).hexdigest()

        journal = OCRJobJournal(
            "a" * 64,
            self.path,
        )

        key = journal.batch_key(
            0,
            1,
            "PP-OCRv6",
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
            [fixture()],
            {
                "pages": [
                    {
                        "width": 200,
                        "height": 200,
                    }
                ]
            },
            page_count=1,
        )

    def run_probe(self, **kwargs):

        return inspect(
            self.pdf,
            self.sha,
            self.path,
            "job-1",
            **kwargs,
        )

    def test_identity_and_displacement(self):

        for dx, status in (
            (0, "near_identity_candidate"),
            (3, "nonidentity"),
        ):

            reg = Mock()

            reg.map_point.side_effect = (
                lambda x, y: SimpleNamespace(
                    x=x + dx,
                    y=y,
                )
            )

            self.assertEqual(
                probe_points(
                    reg,
                    (10, 10, 30, 30),
                )["status"],
                status,
            )

            self.assertEqual(
                reg.map_point.call_count,
                5,
            )

    def test_missing_support(self):

        reg = Mock()

        reg.map_point.return_value = None

        self.assertEqual(
            probe_points(
                reg,
                (10, 10, 30, 30),
            )["status"],
            "local_support_missing",
        )

    def test_cache_only_and_source_unchanged(self):

        before = self.path.read_bytes()

        with patch(
            "backend.paddle_registration_probe.acquire_images",
            return_value=None,
        ) as call:

            self.assertEqual(
                self.run_probe()["status"],
                "ocr_images_unavailable",
            )

            self.assertEqual(
                call.call_args.kwargs["allowed_hosts"],
                (),
            )

        self.assertEqual(
            self.path.read_bytes(),
            before,
        )

    def test_wrong_pdf_hash_before_acquisition(self):

        with patch(
            "backend.paddle_registration_probe.acquire_images"
        ) as call:

            with self.assertRaisesRegex(
                ValueError,
                "SHA-256 mismatch",
            ):

                inspect(
                    self.pdf,
                    "b" * 64,
                    self.path,
                    "job-1",
                )

            call.assert_not_called()

    def test_register_and_filter_tokens(self):

        reg = SimpleNamespace(
            mutual_matches=40,
            inliers=35,
            input_mae=1.0,
            map_point=lambda x, y: SimpleNamespace(
                x=x,
                y=y,
            ),
        )

        images = SimpleNamespace(
            input_image=None,
            ocr_image=None,
        )

        with (
            patch(
                "backend.paddle_registration_probe.acquire_images",
                return_value=images,
            ),
            patch(
                "backend.paddle_registration_probe.register_page",
                return_value=reg,
            ),
        ):

            report = self.run_probe(
                matches=("NaCl",)
            )

        self.assertEqual(
            report["status"],
            "registration_established",
        )

        self.assertEqual(
            len(report["samples"]),
            1,
        )

        self.assertEqual(
            report["samples"][0]["text"],
            "NaCl",
        )

    def test_registration_rejected(self):

        images = SimpleNamespace(
            input_image=None,
            ocr_image=None,
        )

        with (
            patch(
                "backend.paddle_registration_probe.acquire_images",
                return_value=images,
            ),
            patch(
                "backend.paddle_registration_probe.register_page",
                side_effect=RegistrationError("test"),
            ),
        ):

            self.assertEqual(
                self.run_probe()["status"],
                "registration_rejected",
            )


if __name__ == "__main__":
    unittest.main()
