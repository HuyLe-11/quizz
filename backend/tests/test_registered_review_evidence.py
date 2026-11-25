import copy
import hashlib
import unittest

from unittest.mock import Mock, patch

from backend.cloud_review import (
    CloudReviewError,
    CloudReviewService,
)

from backend.engine.registered_review_evidence import (
    registered_evidence,
)


QID = "33333333-3333-4333-8333-333333333333"

PDF = b"%PDF-1.4\nfixture"

LABELS = ["A", "B"]


def legacy(label=None):

    return {
        "detected_answer": label,

        "confidence": .9 if label else 0.,

        "status": (
            "detected"
            if label
            else "not_found"
        ),

        "page_number": 1,

        "mark_coordinates": (
            [1, 2, 3, 4]
            if label
            else None
        ),

        "config_fingerprint": "legacy",
    }


def fixture():

    question = {
        "id": QID,

        "type": "mcp",

        "multiple_choice": False,

        "question_id": 10,

        "source_question_number": 1,

        "page_number": 1,

        "mcp_options": [
            {
                "label": "A",
                "is_correct": True,
            },
            {
                "label": "B",
                "is_correct": False,
            },
        ],

        "red_mark_detection": None,
    }

    entry = {
        "status": "uncertain",

        "page_number": 1,

        "source_question_number": 1,

        "labels": LABELS.copy(),

        "detected_answer": "B",

        "mark_coordinates": [5, 6, 7, 8],

        "registration_method": "local_affine",

        "evidence": "leading_region",
    }

    exam = {
        "source_path": "private.pdf",

        "source_sha256": hashlib.sha256(
            PDF
        ).hexdigest(),

        "extra": {
            "registered_marks": {
                "version": 1,

                "status": "processed",

                "by_question": {
                    QID: entry,
                },

                "pages": {
                    "1": {
                        "status": "processed",
                    },
                },
            },
        },
    }

    return exam, question, entry


class RegisteredReviewTests(unittest.TestCase):

    def setUp(self):

        (
            self.exam,
            self.question,
            self.entry,
        ) = fixture()

        self.repo = Mock()

        self.service = CloudReviewService(
            self.repo
        )

        self.service._owned_question = Mock(
            return_value=(
                self.exam,
                self.question,
            )
        )

        self.service._download_pdf = Mock(
            return_value=PDF
        )

        self.row = None

        self.writes = []

        self.repo._request.side_effect = (
            self.write
        )

        detector = patch(
            "backend.cloud_review.detect_red_mark_pdf",

            return_value=legacy(),
        )

        self.detector = detector.start()

        self.addCleanup(
            detector.stop
        )

        config = patch(
            "backend.cloud_review.RedMarkConfig.from_environment"
        )

        config.start().return_value.fingerprint = (
            "legacy"
        )

        self.addCleanup(
            config.stop
        )

    def write(
        self,
        method,
        path,
        **kwargs,
    ):

        self.assertEqual(
            path,
            "/rest/v1/red_mark_detections",
        )

        data = kwargs["json_data"]

        self.assertNotIn(
            "user_confirmed_answer",
            data,
        )

        self.writes.append(method)

        if method == "POST":

            self.assertIn(
                "resolution=ignore-duplicates",

                kwargs["extra_headers"]["Prefer"],
            )

            if self.row is not None:
                return []

            self.row = {
                "user_confirmed_answer": None,
                **data,
            }

        else:

            self.assertEqual(
                method,
                "PATCH",
            )

            self.assertEqual(
                kwargs["params"],
                {
                    "question_id": f"eq.{QID}",
                },
            )

            if self.row is None:
                return []

            self.row.update(data)

        return [
            copy.deepcopy(self.row)
        ]

    def run_detection(
        self,
        force=False,
    ):

        return self.service.detect_red_mark(
            QID,
            force=force,
        )

    def test_registered_fallback_does_not_change_options(
        self,
    ):

        before = copy.deepcopy(
            self.question
        )

        result = self.run_detection()

        self.assertEqual(
            (
                result["status"],
                result["detected_answer"],
            ),

            ("uncertain", "B"),
        )

        self.assertEqual(
            result["mark_coordinates"],

            [5, 6, 7, 8],
        )

        self.assertEqual(
            self.question,
            before,
        )

        self.assertEqual(
            self.writes,
            ["POST"],
        )

    def test_agreement_stays_review_only(self):

        self.detector.return_value = legacy("B")

        result = self.run_detection()

        self.assertEqual(
            result["status"],
            "uncertain",
        )

        self.assertEqual(
            result["detected_answer"],
            "B",
        )

        self.assertLessEqual(
            result["confidence"],
            .60,
        )

    def test_disagreement_returns_no_label(self):

        self.detector.return_value = legacy("A")

        result = self.run_detection()

        self.assertEqual(
            result["status"],
            "uncertain",
        )

        self.assertIsNone(
            result["detected_answer"]
        )

        self.assertIsNone(
            result["mark_coordinates"]
        )

        self.assertEqual(
            result["confidence"],
            0,
        )

    def test_snapshot_conflict_blocks_legacy(self):

        self.entry["status"] = "conflict"

        self.detector.return_value = legacy("B")

        result = self.run_detection()

        self.assertEqual(
            result["status"],
            "uncertain",
        )

        self.assertIsNone(
            result["detected_answer"]
        )

    def test_invalid_bindings_and_fields(self):

        for field, value in (
            ("labels", ["B", "A"]),

            ("page_number", 2),

            ("source_question_number", 9),

            ("detected_answer", "C"),

            (
                "mark_coordinates",
                [0, 0, float("nan"), 2],
            ),

            (
                "registration_method",
                "unknown",
            ),

            (
                "mark_coordinates",
                [0, 0, 10**400, 2],
            ),
        ):

            with self.subTest(field=field):

                exam, question, entry = fixture()

                entry[field] = value

                self.assertIsNone(
                    registered_evidence(
                        exam,
                        question,
                        LABELS,
                    )
                )

        self.question["multiple_choice"] = True

        self.assertIsNone(
            registered_evidence(
                self.exam,
                self.question,
                LABELS,
            )
        )

    def test_fingerprint_refresh_and_cache_hit(self):

        self.row = {
            **legacy(),

            "question_id": QID,

            "user_confirmed_answer": "A",
        }

        self.question["red_mark_detection"] = (
            copy.deepcopy(self.row)
        )

        result = self.run_detection()

        self.assertNotEqual(
            result["config_fingerprint"],
            "legacy",
        )

        self.assertEqual(
            result["user_confirmed_answer"],
            "A",
        )

        self.question["red_mark_detection"] = (
            copy.deepcopy(self.row)
        )

        self.assertTrue(
            self.run_detection()["cached"]
        )

        self.detector.assert_called_once()

        self.entry["detected_answer"] = "A"

        self.assertFalse(
            self.run_detection()["cached"]
        )

        self.assertEqual(
            self.detector.call_count,
            2,
        )

    def test_force_preserves_newer_confirmation(self):

        self.row = {
            **legacy(),

            "question_id": QID,

            "user_confirmed_answer": "B",
        }

        self.question["red_mark_detection"] = {
            **self.row,

            "user_confirmed_answer": "A",
        }

        result = self.run_detection(
            force=True
        )

        self.assertEqual(
            result["user_confirmed_answer"],
            "B",
        )

        self.assertEqual(
            self.writes,
            ["PATCH"],
        )

    def test_concurrent_insert_preserves_confirmation(self):

        self.row = {
            **legacy(),

            "question_id": QID,

            "user_confirmed_answer": "A",
        }

        result = self.run_detection()

        self.assertEqual(
            result["user_confirmed_answer"],
            "A",
        )

        self.assertEqual(
            self.writes,
            ["POST", "PATCH"],
        )

    def test_source_mismatch_rejects_before_detection(self):

        self.service._download_pdf.return_value = (
            PDF + b"changed"
        )

        with self.assertRaises(
            CloudReviewError
        ) as error:

            self.run_detection()

        self.assertEqual(
            error.exception.status_code,
            409,
        )

        self.detector.assert_not_called()

        self.repo._request.assert_not_called()

    def test_missing_snapshot_preserves_legacy_contract(self):

        self.exam["extra"] = {}

        self.detector.return_value = legacy("A")

        result = self.run_detection()

        self.assertEqual(
            result["status"],
            "detected",
        )

        self.assertEqual(
            result["config_fingerprint"],
            "legacy",
        )

        self.assertEqual(
            result["detected_answer"],
            "A",
        )

    def test_missing_update_not_reported_as_saved(self):

        self.question["red_mark_detection"] = (
            legacy()
        )

        with self.assertRaises(
            CloudReviewError
        ) as error:

            self.run_detection(
                force=True
            )

        self.assertEqual(
            error.exception.status_code,
            409,
        )

        self.assertEqual(
            self.writes,
            ["PATCH"],
        )


if __name__ == "__main__":
    unittest.main()