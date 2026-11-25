import tempfile
import unittest

from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import requests

from backend.engine import (
    ocr_image_acquisition as acq,
)

from backend.engine.ocr_image_cache import (
    OCRImageCache,
    OCRPageRef,
)

from backend.engine.ocr_job_journal import (
    OCRJobJournal,
)


HOST = "images.example.com"

FIRST = (
    "https://images.example.com/"
    "input?signature=private"
)

SECOND = (
    "https://images.example.com/"
    "ocr?signature=private"
)


def image(w, h, ext=".png"):

    ok, data = cv2.imencode(
        ext,

        np.full(
            (h, w, 3),
            230,
            np.uint8,
        ),
    )

    assert ok

    return data.tobytes()


class ImageAcquisitionTests(unittest.TestCase):

    def setUp(self):

        temp = tempfile.TemporaryDirectory()

        self.addCleanup(
            temp.cleanup
        )

        path = Path(
            temp.name
        )

        self.journal = OCRJobJournal(
            "a" * 64,

            path / "jobs.sqlite3",
        )

        self.key = self.journal.batch_key(
            0,

            1,

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

            1,

            0,

            claim,

            "job-1",
        )

        self.page = {
            "pruned_result": {
                "rec_boxes": [
                    [1, 1, 20, 20]
                ],
            },

            "input_image_url": FIRST,

            "ocr_image_url": SECOND,
        }

        self.finish()

        self.cache = OCRImageCache(
            path / "images.sqlite3"
        )

        self.first = image(
            128,
            128,
            ".jpeg",
        )

        self.second = image(
            256,
            128,
        )

    def finish(self):

        self.journal.finish_with_result(
            self.key,

            self.ref.claim_id,

            self.ref.job_id,

            [self.page],

            {
                "pages": [
                    {
                        "width": 128,
                        "height": 128,
                    }
                ],
            },

            page_count=1,
        )

    def acquire(
        self,
        hosts=(HOST,),
    ):

        return acq.acquire_images(
            self.journal,

            self.ref,

            self.cache,

            allowed_hosts=hosts,
        )

    def test_download_and_cache_hit(self):

        with patch.object(
            acq,
            "_fetch",

            side_effect=[
                self.first,
                self.second,
            ],

        ) as fetch:

            pair = self.acquire()

            self.assertIsNotNone(pair)

            self.assertEqual(
                pair.ocr_image.shape,

                (128, 256, 3),
            )

            self.assertEqual(
                fetch.call_count,
                2,
            )

            self.assertEqual(
                [
                    c.args[0]

                    for c in fetch.call_args_list
                ],

                [FIRST, SECOND],
            )

            self.assertIsNotNone(
                self.acquire(
                    hosts=(),
                )
            )

            self.assertEqual(
                fetch.call_count,
                2,
            )

    def test_disabled_by_default(self):

        with (
            patch.dict(
                "os.environ",

                {
                    "PADDLEOCR_IMAGE_ALLOWED_HOSTS": "",
                },
            ),

            patch.object(
                acq,
                "_fetch",
            ) as fetch,
        ):

            self.assertIsNone(
                acq.acquire_images(
                    self.journal,

                    self.ref,

                    self.cache,
                )
            )

            fetch.assert_not_called()

    def test_both_urls_validated_before_get(self):

        for url in (
            "http://images.example.com/a",

            "https://evil.example/a",

            "https://images.example.com.evil.example/a",

            "https://images.example.com@evil.example/a",

            "https://images.example.com:444/a",

            "https://images.example.com/a#x",

            "https://images.example.com\\@evil.example/a",

            None,
        ):

            self.page[
                "ocr_image_url"
            ] = url

            self.finish()

            with patch.object(
                acq,
                "_fetch",
            ) as fetch:

                self.assertIsNone(
                    self.acquire()
                )

                fetch.assert_not_called()

    def test_missing_result_never_downloads(self):

        other = OCRJobJournal(
            "b" * 64,

            self.journal.path,
        )

        with patch.object(
            acq,
            "_fetch",
        ) as fetch:

            self.assertIsNone(
                acq.acquire_images(
                    other,

                    self.ref,

                    self.cache,

                    allowed_hosts=(HOST,),
                )
            )

            fetch.assert_not_called()

    def test_second_download_failure_no_partial_pair(self):

        with patch.object(
            acq,
            "_fetch",

            side_effect=[
                self.first,
                requests.Timeout("secret"),
            ],
        ):

            self.assertIsNone(
                self.acquire()
            )

        self.assertIsNone(
            self.cache.load(
                self.journal,
                self.ref,
            )
        )

    def test_wrong_dimensions_rejected_before_decode(self):

        wrong = image(
            256,
            256,
        )

        with patch.object(
            acq.cv2,
            "imdecode",
        ) as decode:

            with self.assertRaises(
                acq.AcquisitionError
            ):

                acq._as_png(
                    wrong,
                    128,
                    128,
                )

            decode.assert_not_called()

    def test_result_replaced_during_download(self):

        def download(url, deadline):

            if url == FIRST:

                return self.first

            self.page[
                "pruned_result"
            ][
                "rec_boxes"
            ] = [
                [2, 2, 20, 20]
            ]

            self.finish()

            return self.second

        with patch.object(
            acq,
            "_fetch",

            side_effect=download,
        ):

            self.assertIsNone(
                self.acquire()
            )

        self.assertIsNone(
            self.cache.load(
                self.journal,

                self.ref,
            )
        )

    def response(
        self,

        status=200,

        headers=None,

        chunks=(),
    ):

        session = MagicMock()

        session.__enter__.return_value = (
            session
        )

        response = (
            session.get.return_value
            .__enter__.return_value
        )

        response.status_code = status

        response.headers = (
            headers

            or {
                "Content-Type": "image/png",
            }
        )

        response.iter_content.return_value = (
            iter(chunks)
        )

        return session

    def test_http_get_options(self):

        session = self.response(
            chunks=[b"abc"]
        )

        with patch.object(
            acq.requests,

            "Session",

            return_value=session,
        ):

            self.assertEqual(
                acq._fetch(
                    FIRST,

                    float("inf"),
                ),

                b"abc",
            )

        self.assertFalse(
            session.trust_env
        )

        self.assertFalse(
            session.get.call_args.kwargs[
                "allow_redirects"
            ]
        )

        self.assertTrue(
            session.get.call_args.kwargs[
                "stream"
            ]
        )

        self.assertEqual(
            session.get.call_args.args,

            (FIRST,),
        )

    def test_http_redirect_or_expired_url(self):

        for status in (
            302,
            403,
            404,
        ):

            session = self.response(
                status=status
            )

            with patch.object(
                acq.requests,

                "Session",

                return_value=session,
            ):

                with self.assertRaises(
                    acq.AcquisitionError
                ):

                    acq._fetch(
                        FIRST,

                        float("inf"),
                    )

    def test_http_size_limits(self):

        for headers, chunks in (

            (
                {
                    "Content-Type": "image/png",
                    "Content-Length": "11",
                },

                [],
            ),

            (
                {
                    "Content-Type": "image/png",
                },

                [
                    b"123456",
                    b"78901",
                ],
            ),

            (
                {
                    "Content-Type": "image/png",
                    "Content-Length": "5",
                },

                [b"12"],
            ),
        ):

            session = self.response(
                headers=headers,

                chunks=chunks,
            )

            with (
                patch.object(
                    acq,

                    "MAX_IMAGE_BYTES",

                    10,
                ),

                patch.object(
                    acq.requests,

                    "Session",

                    return_value=session,
                ),
            ):

                with self.assertRaises(
                    acq.AcquisitionError
                ):

                    acq._fetch(
                        FIRST,

                        float("inf"),
                    )

    def test_http_non_image_and_encoding(self):

        for headers in (

            {
                "Content-Type": "text/html",
            },

            {
                "Content-Type": "image/png",
                "Content-Encoding": "gzip",
            },
        ):

            session = self.response(
                headers=headers
            )

            with patch.object(
                acq.requests,

                "Session",

                return_value=session,
            ):

                with self.assertRaises(
                    acq.AcquisitionError
                ):

                    acq._fetch(
                        FIRST,

                        float("inf"),
                    )

    def test_deadline_before_request(self):

        with patch.object(
            acq.requests,

            "Session",
        ) as session:

            with self.assertRaises(
                acq.AcquisitionError
            ):

                acq._fetch(
                    FIRST,

                    0,
                )

            session.assert_not_called()


if __name__ == "__main__":

    unittest.main()