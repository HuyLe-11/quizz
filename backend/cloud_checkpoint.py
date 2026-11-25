"""Short-lived local checkpoint for completed cloud PDF preprocessing.

Only OCR output, parsed questions and crop bytes are cached; the original PDF
and authentication token are not stored. A retry must supply the same PDF.
"""

import hashlib
import json
import logging
import os
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from uuid import UUID

logger = logging.getLogger("quizpdf")

CHECKPOINT_ROOT = (
    Path(__file__).resolve().parent
    / "data" / "storage" / "cloud-checkpoints"
)
CHECKPOINT_TTL_SECONDS = 24 * 60 * 60
CHECKPOINT_VERSION = 1
MAX_MANIFEST_BYTES = 20 * 1024 * 1024
MAX_CROP_BYTES = 12 * 1024 * 1024
MAX_CROPS = 300


@dataclass(frozen=True)
class CachedExamBundle:
    exam: dict[str, Any]
    questions: list[dict[str, Any]]
    crop_images: dict[str, bytes]


class CloudOCRCheckpoint:
    """Owner-scoped cache keyed by user, PDF content, filename and OCR model."""

    def __init__(
        self,
        user_id: str,
        filename: str,
        model: str,
        pdf_contents: bytes,
    ) -> None:
        self.user_id = str(UUID(user_id))
        self.filename = filename
        self.model = model
        self.source_sha256 = hashlib.sha256(pdf_contents).hexdigest()

        identity = json.dumps(
            {
                "user_id": self.user_id,
                "filename": self.filename,
                "model": self.model,
                "source_sha256": self.source_sha256,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")

        key = hashlib.sha256(identity).hexdigest()
        self.path = CHECKPOINT_ROOT / f"{key}.zip"

    def save(
        self,
        exam: dict[str, Any],
        questions: list[dict[str, Any]],
        crop_images: Mapping[str, bytes],
    ) -> None:
        """Atomically store the prepared bundle before any Storage upload."""
        if exam.get("source_sha256") != self.source_sha256:
            raise ValueError("Checkpoint PDF digest does not match.")

        if len(crop_images) > MAX_CROPS:
            raise ValueError("Too many checkpoint crop images.")

        entries: list[dict[str, Any]] = []

        for index, (path, image) in enumerate(
            sorted(crop_images.items())
        ):
            if not isinstance(path, str):
                raise ValueError("Invalid checkpoint crop path.")

            relative = PurePosixPath(path)

            if (
                not path
                or path.startswith("/")
                or "\\" in path
                or ".." in relative.parts
                or not isinstance(image, bytes)
                or len(image) > MAX_CROP_BYTES
            ):
                raise ValueError("Invalid checkpoint crop image.")

            entries.append(
                {
                    "path": path,
                    "entry": f"crops/{index:04d}.bin",
                    "sha256": hashlib.sha256(image).hexdigest(),
                    "size": len(image),
                }
            )

        manifest = {
            "version": CHECKPOINT_VERSION,
            "created_at": time.time(),
            "user_id": self.user_id,
            "filename": self.filename,
            "model": self.model,
            "source_sha256": self.source_sha256,
            "exam": exam,
            "questions": questions,
            "crops": entries,
        }

        manifest_bytes = json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")

        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ValueError(
                "Checkpoint metadata exceeds the size limit."
            )

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

        if os.name != "nt":
            self.path.parent.chmod(0o700)

        fd, temporary = tempfile.mkstemp(
            prefix=".checkpoint-",
            suffix=".tmp",
            dir=self.path.parent,
        )
        os.close(fd)

        try:
            if os.name != "nt":
                os.chmod(temporary, 0o600)

            with zipfile.ZipFile(
                temporary,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=3,
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    manifest_bytes,
                )

                for entry in entries:
                    archive.writestr(
                        entry["entry"],
                        crop_images[entry["path"]],
                    )

            os.replace(temporary, self.path)

            logger.info(
                "cloud=checkpoint status=saved images=%d",
                len(entries),
            )
        finally:
            Path(temporary).unlink(missing_ok=True)

    def load(self) -> CachedExamBundle | None:
        """Validate owner, content identity, TTL and crop integrity."""
        if not self.path.is_file():
            return None

        try:
            with zipfile.ZipFile(self.path, "r") as archive:
                info = archive.getinfo("manifest.json")

                if info.file_size > MAX_MANIFEST_BYTES:
                    raise ValueError(
                        "Checkpoint manifest is too large."
                    )

                manifest = json.loads(
                    archive.read("manifest.json")
                )

                if not isinstance(manifest, dict):
                    raise ValueError(
                        "Checkpoint manifest is invalid."
                    )

                if (
                    manifest.get("version") != CHECKPOINT_VERSION
                    or manifest.get("user_id") != self.user_id
                    or manifest.get("filename") != self.filename
                    or manifest.get("model") != self.model
                    or manifest.get("source_sha256")
                    != self.source_sha256
                ):
                    raise ValueError(
                        "Checkpoint identity mismatch."
                    )

                age = time.time() - float(
                    manifest["created_at"]
                )

                if not 0 <= age <= CHECKPOINT_TTL_SECONDS:
                    raise ValueError(
                        "Checkpoint expired or has invalid time."
                    )

                exam = manifest["exam"]
                questions = manifest["questions"]
                entries = manifest["crops"]

                if (
                    not isinstance(exam, dict)
                    or exam.get("source_sha256")
                    != self.source_sha256
                    or not isinstance(questions, list)
                    or not isinstance(entries, list)
                    or len(entries) > MAX_CROPS
                    or any(
                        not isinstance(q, dict)
                        for q in questions
                    )
                ):
                    raise ValueError(
                        "Checkpoint metadata is invalid."
                    )

                images: dict[str, bytes] = {}

                for index, entry in enumerate(entries):
                    expected_entry = f"crops/{index:04d}.bin"

                    if (
                        not isinstance(entry, dict)
                        or entry.get("entry")
                        != expected_entry
                    ):
                        raise ValueError(
                            "Checkpoint crop entry is invalid."
                        )

                    path = entry.get("path")

                    if (
                        not isinstance(path, str)
                        or path in images
                    ):
                        raise ValueError(
                            "Checkpoint crop path is invalid."
                        )

                    info = archive.getinfo(
                        expected_entry
                    )

                    if info.file_size > MAX_CROP_BYTES:
                        raise ValueError(
                            "Checkpoint crop is too large."
                        )

                    contents = archive.read(
                        expected_entry
                    )

                    if (
                        len(contents) != entry.get("size")
                        or hashlib.sha256(
                            contents
                        ).hexdigest() != entry.get("sha256")
                    ):
                        raise ValueError(
                            "Checkpoint crop checksum mismatch."
                        )

                    images[path] = contents

                return CachedExamBundle(
                    exam,
                    questions,
                    images,
                )

        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            zipfile.BadZipFile,
            RuntimeError,
        ) as exc:
            logger.warning(
                "cloud=checkpoint status=invalid error_type=%s",
                type(exc).__name__,
            )
            self.delete()
            return None

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)