"""Offline safety tests for manual abandoned-checkpoint cleanup."""

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from backend.cloud_checkpoint_cleanup import (
    CleanupSafetyError,
    cleanup_checkpoint,
    find_expired_checkpoints,
)

NOW = 1_800_000_000.0
USER = "11111111-1111-4111-8111-111111111111"
EXAM = "22222222-2222-4222-8222-222222222222"
SOURCE_SHA = hashlib.sha256(b"example PDF").hexdigest()


def create_checkpoint(
    root,
    *,
    age_hours=72,
    corrupt_crop=False,
):
    identity = {
        "user_id": USER,
        "filename": "example.pdf",
        "model": "PaddleOCR-VL-1.6",
        "source_sha256": SOURCE_SHA,
    }

    key = hashlib.sha256(
        json.dumps(
            identity,
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()

    path = root / f"{key}.zip"
    data = b"image-bytes"

    manifest = {
        "version": 1,
        "created_at": NOW - age_hours * 3600,
        **identity,
        "exam": {
            "id": EXAM,
            "source_sha256": SOURCE_SHA,
        },
        "questions": [],
        "crops": [
            {
                "path": f"{EXAM}/question-test.png",
                "entry": "crops/0000.bin",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        ],
    }

    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest),
        )

        archive.writestr(
            "crops/0000.bin",
            (
                b"wrong-content"
                if corrupt_crop
                else data
            ),
        )

    old = NOW - age_hours * 3600
    os.utime(path, (old, old))
    return path


class FakeRepository:
    def __init__(
        self,
        *,
        user_id=USER,
        token_owner=USER,
        exam_rows=None,
        fail_at=None,
    ):
        self.user_id = user_id
        self.token_owner = token_owner
        self.exam_rows = (
            exam_rows
            if exam_rows is not None
            else []
        )
        self.fail_at = fail_at
        self.deleted = []
        self.reads = []

    def _request(
        self,
        method,
        path,
        *,
        params=None,
    ):
        self.reads.append((method, path))

        if path == "/auth/v1/user":
            return {"id": self.token_owner}

        if path == "/rest/v1/exams":
            return self.exam_rows

        raise AssertionError(
            "Unexpected request"
        )

    def _remove_objects(self, paths):
        if self.fail_at == "delete":
            raise RuntimeError(
                "Simulated Storage outage"
            )

        self.deleted.extend(paths)


class CheckpointCleanupTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def candidates(
        self,
        *,
        age_hours=72,
        corrupt_crop=False,
    ):
        path = create_checkpoint(
            self.root,
            age_hours=age_hours,
            corrupt_crop=corrupt_crop,
        )

        found = find_expired_checkpoints(
            root=self.root,
            now=NOW,
        )

        return path, found

    def test_scan_is_read_only(self):
        path, found = self.candidates()

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].exam_id, EXAM)
        self.assertTrue(path.exists())

    def test_fresh_checkpoint_is_not_eligible(self):
        path, found = self.candidates(
            age_hours=24
        )

        self.assertEqual(found, [])
        self.assertTrue(path.exists())

    def test_wrong_confirmation_does_not_access_remote(self):
        path, found = self.candidates()
        db = FakeRepository()

        with self.assertRaises(CleanupSafetyError):
            cleanup_checkpoint(
                db,
                found[0],
                confirm_exam_id="wrong",
                now=NOW,
            )

        self.assertEqual(db.reads, [])
        self.assertTrue(path.exists())

    def test_jwt_owner_mismatch_blocks_delete(self):
        path, found = self.candidates()

        db = FakeRepository(
            token_owner=(
                "33333333-3333-4333-8333-333333333333"
            ),
        )

        with self.assertRaisesRegex(
            CleanupSafetyError,
            "JWT",
        ):
            cleanup_checkpoint(
                db,
                found[0],
                confirm_exam_id=EXAM,
                now=NOW,
            )

        self.assertEqual(db.deleted, [])
        self.assertTrue(path.exists())

    def test_committed_exam_never_deleted(self):
        path, found = self.candidates()

        db = FakeRepository(
            exam_rows=[{"id": EXAM}],
        )

        with self.assertRaisesRegex(
            CleanupSafetyError,
            "exists",
        ):
            cleanup_checkpoint(
                db,
                found[0],
                confirm_exam_id=EXAM,
                now=NOW,
            )

        self.assertEqual(db.deleted, [])
        self.assertTrue(path.exists())

    def test_only_specific_pdf_and_crop_variants_are_deleted(self):
        path, found = self.candidates()
        db = FakeRepository()

        count = cleanup_checkpoint(
            db,
            found[0],
            confirm_exam_id=EXAM,
            now=NOW,
        )

        self.assertEqual(count, 3)

        self.assertEqual(
            set(db.deleted),
            {
                f"{USER}/pdfs/{EXAM}.pdf",
                (
                    f"{USER}/assets/{EXAM}/"
                    "question-test.png"
                ),
                (
                    f"{USER}/assets/{EXAM}/"
                    "question-test.jpg"
                ),
            },
        )

        self.assertFalse(path.exists())

    def test_corrupt_crop_keeps_checkpoint_and_remote_objects(self):
        path, found = self.candidates(
            corrupt_crop=True
        )

        db = FakeRepository()

        with self.assertRaisesRegex(
            CleanupSafetyError,
            "checksum",
        ):
            cleanup_checkpoint(
                db,
                found[0],
                confirm_exam_id=EXAM,
                now=NOW,
            )

        self.assertEqual(db.deleted, [])
        self.assertTrue(path.exists())

    def test_failed_delete_keeps_checkpoint_for_retry(self):
        path, found = self.candidates()

        db = FakeRepository(
            fail_at="delete"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "outage",
        ):
            cleanup_checkpoint(
                db,
                found[0],
                confirm_exam_id=EXAM,
                now=NOW,
            )

        self.assertTrue(path.exists())

    def test_changed_file_since_scan_blocks_cleanup(self):
        path, found = self.candidates()

        os.utime(path, (NOW, NOW))
        db = FakeRepository()

        with self.assertRaises(CleanupSafetyError):
            cleanup_checkpoint(
                db,
                found[0],
                confirm_exam_id=EXAM,
                now=NOW,
            )

        self.assertEqual(db.deleted, [])


if __name__ == "__main__":
    unittest.main()
