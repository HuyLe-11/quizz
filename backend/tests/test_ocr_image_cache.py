import sqlite3
import tempfile
import unittest

from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from backend.engine.ocr_image_cache import (
    OCRImageCache,
    OCRPageRef,
    ImageCacheError,
)

from backend.engine.ocr_job_journal import (
    OCRJobJournal,
)


def png(width, height):

    ok, data = cv2.imencode(
        ".png",

        np.full(
            (height, width, 3),
            255,
            np.uint8,
        ),
    )

    assert ok

    return data.tobytes()


class OCRImageCacheTests(unittest.TestCase):

    def setUp(self):

        folder = tempfile.TemporaryDirectory()

        self.addCleanup(
            folder.cleanup
        )

        self.path = Path(
            folder.name
        )

        self.journal = OCRJobJournal(
            "a" * 64,

            self.path / "journal.sqlite3",
        )

        self.key = self.journal.batch_key(
            0,
            2,
            "PP-OCRv6",
        )

        claim = self.journal.reserve(
            self.key
        ).record.claim_id

        self.journal.accept(
            self.key,
            claim,
            "job-1",
        )

        self.ref = OCRPageRef(
            0,
            2,
            0,
            claim,
            "job-1",
        )

        self.other = OCRPageRef(
            0,
            2,
            1,
            claim,
            "job-1",
        )

        self.pages = [
            {
                "pruned_result": {
                    "rec_boxes": [
                        [1, 1, 20, 20]
                    ],

                    "rec_texts": [
                        str(i)
                    ],
                }
            }

            for i in range(2)
        ]

        self.info = {
            "pages": [
                {
                    "width": 128,
                    "height": 128,
                }
            ] * 2,
        }

        self.journal.finish_with_result(
            self.key,
            claim,
            "job-1",
            self.pages,
            self.info,
            page_count=2,
        )

        self.first = png(
            128,
            128,
        )

        self.second = png(
            256,
            128,
        )

        self.cache = OCRImageCache(
            self.path / "images.sqlite3"
        )

    def store(self, ref=None):

        self.cache.store(
            self.journal,

            ref or self.ref,

            self.first,
            self.second,
        )

    def test_round_trip_after_reopen(self):

        self.store()

        pair = OCRImageCache(
            self.cache.path
        ).load(
            self.journal,
            self.ref,
        )

        self.assertIsNotNone(pair)

        self.assertEqual(
            pair.input_image.shape,
            (128, 128, 3),
        )

        self.assertEqual(
            pair.ocr_image.shape,
            (128, 256, 3),
        )

        self.assertTrue(
            np.all(
                pair.input_image == 255
            )
        )

    def test_scope_page_and_job_isolation(self):

        self.store()

        self.assertIsNone(
            self.cache.load(
                self.journal,
                self.other,
            )
        )

        wrong = OCRPageRef(
            0,
            2,
            0,
            self.ref.claim_id,
            "job-other",
        )

        self.assertIsNone(
            self.cache.load(
                self.journal,
                wrong,
            )
        )

        other_owner = OCRJobJournal(
            "b" * 64,
            self.journal.path,
        )

        self.assertIsNone(
            self.cache.load(
                other_owner,
                self.ref,
            )
        )

    def test_missing_result_blocks_access(self):

        self.store()

        with closing(
            sqlite3.connect(
                self.journal.path
            )
        ) as conn, conn:

            conn.execute(
                "DELETE FROM ocr_results"
            )

        self.assertIsNone(
            self.cache.load(
                self.journal,
                self.ref,
            )
        )

        with self.assertRaises(
            ImageCacheError
        ):

            self.store()

    def test_changed_geometry_invalidates_pair(self):

        self.store()

        self.pages[0]["pruned_result"]["rec_boxes"] = [
            [2, 2, 21, 21]
        ]

        self.journal.finish_with_result(
            self.key,
            self.ref.claim_id,
            self.ref.job_id,
            self.pages,
            self.info,
            page_count=2,
        )

        self.assertIsNone(
            self.cache.load(
                self.journal,
                self.ref,
            )
        )

    def test_bad_images_leave_no_pair(self):

        for first, second in (
            (
                b"bad",
                self.second,
            ),
            (
                self.first,
                self.first,
            ),
        ):

            with self.assertRaises(
                ImageCacheError
            ):

                self.cache.store(
                    self.journal,
                    self.ref,
                    first,
                    second,
                )

        self.assertIsNone(
            self.cache.load(
                self.journal,
                self.ref,
            )
        )

    def test_corrupt_blob_rejected(self):

        self.store()

        with closing(
            sqlite3.connect(
                self.cache.path
            )
        ) as conn, conn:

            conn.execute(
                """
                UPDATE image_pairs
                SET input_sha256 = 'bad'
                """
            )

        self.assertIsNone(
            self.cache.load(
                self.journal,
                self.ref,
            )
        )

    def test_ttl_and_prune(self):

        self.cache = OCRImageCache(
            self.cache.path,
            ttl_seconds=10,
        )

        with patch(
            "backend.engine.ocr_image_cache.time.time",
            return_value=100,
        ):

            self.store()

        with patch(
            "backend.engine.ocr_image_cache.time.time",
            return_value=110,
        ):

            self.assertIsNone(
                self.cache.load(
                    self.journal,
                    self.ref,
                )
            )

            self.cache.prune()

        with closing(
            sqlite3.connect(
                self.cache.path
            )
        ) as conn:

            self.assertEqual(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM image_pairs
                    """
                ).fetchone()[0],

                0,
            )

    def test_budget_evicts_old_pair(self):

        self.cache = OCRImageCache(
            self.cache.path,

            max_bytes=(
                len(self.first)
                + len(self.second)
            ),
        )

        self.store()

        self.store(
            self.other
        )

        self.assertIsNone(
            self.cache.load(
                self.journal,
                self.ref,
            )
        )

        self.assertIsNotNone(
            self.cache.load(
                self.journal,
                self.other,
            )
        )

    def test_pair_larger_than_budget(self):

        self.cache = OCRImageCache(
            self.cache.path,

            max_bytes=1,
        )

        with self.assertRaises(
            ImageCacheError
        ):

            self.store()

        self.assertIsNone(
            self.cache.load(
                self.journal,
                self.ref,
            )
        )


if __name__ == "__main__":
    unittest.main()