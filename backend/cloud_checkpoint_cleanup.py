"""Manual, fail-closed cleanup for abandoned QuizPDF cloud upload checkpoints.

Default CLI mode only scans local files. Deletion requires a 48-hour-old
checkpoint, a matching authenticated user and an explicit exam ID.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
import os
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

CHECKPOINT_ROOT = (
    Path(__file__).resolve().parent
    / "data" / "storage" / "cloud-checkpoints"
)
MIN_AGE_HOURS = 48
MAX_MANIFEST_BYTES = 20 * 1024 * 1024
MAX_CROP_BYTES = 12 * 1024 * 1024
MAX_CROPS = 300
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CleanupSafetyError(RuntimeError):
    """Stop if ownership, data integrity or database state is uncertain."""


@dataclass(frozen=True)
class ExpiredCheckpoint:
    path: Path
    user_id: str
    exam_id: str
    created_at: float
    modified_ns: int
    age_hours: float


def _read_bounded(
    archive: zipfile.ZipFile,
    name: str,
    limit: int,
) -> bytes:
    info = archive.getinfo(name)

    if info.file_size > limit:
        raise CleanupSafetyError(
            f"Oversized checkpoint member: {name}"
        )

    with archive.open(info) as source:
        data = source.read(limit + 1)

    if len(data) > limit:
        raise CleanupSafetyError(
            f"Oversized checkpoint member: {name}"
        )

    return data


def _inspect(
    path: Path,
    *,
    now: float,
    min_age_hours: int,
) -> ExpiredCheckpoint:
    if path.is_symlink() or not path.is_file():
        raise CleanupSafetyError(
            "Checkpoint must be a regular file."
        )

    stat = path.stat()

    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(
            _read_bounded(
                archive,
                "manifest.json",
                MAX_MANIFEST_BYTES,
            )
        )

    if (
        not isinstance(manifest, dict)
        or manifest.get("version") != 1
    ):
        raise CleanupSafetyError(
            "Unsupported checkpoint manifest."
        )

    try:
        user_id = str(UUID(manifest["user_id"]))
        exam = manifest["exam"]
        exam_id = str(UUID(exam["id"]))
        created_at = float(manifest["created_at"])
        filename = manifest["filename"]
        model = manifest["model"]
        source_sha256 = manifest["source_sha256"]

    except (
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
    ) as exc:
        raise CleanupSafetyError(
            "Invalid checkpoint identity."
        ) from exc

    if (
        not math.isfinite(created_at)
        or not isinstance(filename, str)
        or not filename
        or not isinstance(model, str)
        or not model
        or not isinstance(source_sha256, str)
        or not SHA256_RE.fullmatch(source_sha256)
        or not isinstance(exam, dict)
        or exam.get("source_sha256") != source_sha256
        or manifest["user_id"] != user_id
        or exam["id"] != exam_id
    ):
        raise CleanupSafetyError(
            "Checkpoint identity mismatch."
        )

    identity = json.dumps(
        {
            "user_id": user_id,
            "filename": filename,
            "model": model,
            "source_sha256": source_sha256,
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")

    if hashlib.sha256(identity).hexdigest() != path.stem:
        raise CleanupSafetyError(
            "Checkpoint filename does not match its identity."
        )

    age_hours = (
        now - max(created_at, stat.st_mtime)
    ) / 3600

    if age_hours < min_age_hours:
        raise CleanupSafetyError(
            "Checkpoint is not old enough for cleanup."
        )

    return ExpiredCheckpoint(
        path,
        user_id,
        exam_id,
        created_at,
        stat.st_mtime_ns,
        age_hours,
    )


def find_expired_checkpoints(
    *,
    root: Path = CHECKPOINT_ROOT,
    now: float | None = None,
    min_age_hours: int = MIN_AGE_HOURS,
) -> list[ExpiredCheckpoint]:
    """Read-only inventory. Invalid checkpoint files are skipped."""
    if min_age_hours < MIN_AGE_HOURS:
        raise ValueError(
            "Minimum cleanup age is 48 hours."
        )

    if not root.is_dir():
        return []

    current = time.time() if now is None else now
    results = []

    for path in sorted(root.glob("*.zip")):
        try:
            candidate = _inspect(
                path,
                now=current,
                min_age_hours=min_age_hours,
            )
            results.append(candidate)

        except (
            OSError,
            ValueError,
            KeyError,
            zipfile.BadZipFile,
            CleanupSafetyError,
        ):
            continue

    return results


def _object_paths(
    checkpoint: ExpiredCheckpoint,
) -> list[str]:
    """Derive exact paths from validated checkpoint crop entries."""
    prefix = (
        f"{checkpoint.user_id}/assets/"
        f"{checkpoint.exam_id}/"
    )

    paths = {
        (
            f"{checkpoint.user_id}/pdfs/"
            f"{checkpoint.exam_id}.pdf"
        )
    }

    with zipfile.ZipFile(checkpoint.path) as archive:
        manifest = json.loads(
            _read_bounded(
                archive,
                "manifest.json",
                MAX_MANIFEST_BYTES,
            )
        )

        entries = manifest.get("crops")

        if (
            not isinstance(entries, list)
            or len(entries) > MAX_CROPS
        ):
            raise CleanupSafetyError(
                "Invalid checkpoint crop list."
            )

        expected_names = {"manifest.json"}
        used = set()

        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise CleanupSafetyError(
                    "Invalid crop entry."
                )

            member = f"crops/{index:04d}.bin"
            original = entry.get("path")

            if (
                entry.get("entry") != member
                or not isinstance(original, str)
            ):
                raise CleanupSafetyError(
                    "Unexpected crop entry name."
                )

            relative = PurePosixPath(original)

            if (
                len(relative.parts) != 2
                or relative.parts[0] != checkpoint.exam_id
                or not relative.name.startswith("question-")
                or relative.suffix != ".png"
                or original in used
                or not isinstance(entry.get("size"), int)
                or not isinstance(entry.get("sha256"), str)
                or not SHA256_RE.fullmatch(entry["sha256"])
            ):
                raise CleanupSafetyError(
                    "Unsafe crop path or metadata."
                )

            contents = _read_bounded(
                archive,
                member,
                MAX_CROP_BYTES,
            )

            if (
                len(contents) != entry["size"]
                or hashlib.sha256(contents).hexdigest()
                != entry["sha256"]
            ):
                raise CleanupSafetyError(
                    "Crop checksum mismatch; refusing cleanup."
                )

            used.add(original)
            expected_names.add(member)

            # Earlier uploads may be PNG; optimized ones may be JPEG.
            paths.add(prefix + relative.name)
            paths.add(
                prefix
                + relative.with_suffix(".jpg").name
            )

        archive_names = archive.namelist()

        if (
            set(archive_names) != expected_names
            or len(archive_names) != len(expected_names)
        ):
            raise CleanupSafetyError(
                "Unexpected files in checkpoint archive."
            )

    return sorted(paths)


def cleanup_checkpoint(
    repository: Any,
    checkpoint: ExpiredCheckpoint,
    *,
    confirm_exam_id: str,
    now: float | None = None,
    min_age_hours: int = MIN_AGE_HOURS,
) -> int:
    """Delete validated abandoned exam objects, never a committed exam."""
    if min_age_hours < MIN_AGE_HOURS:
        raise CleanupSafetyError(
            "Cleanup requires at least 48 hours."
        )

    if confirm_exam_id != checkpoint.exam_id:
        raise CleanupSafetyError(
            "Explicit exam ID confirmation is required."
        )

    current = time.time() if now is None else now

    latest = _inspect(
        checkpoint.path,
        now=current,
        min_age_hours=min_age_hours,
    )

    if latest != checkpoint:
        raise CleanupSafetyError(
            "Checkpoint changed since inventory."
        )

    # Complete local integrity validation before remote operations.
    paths = _object_paths(latest)

    if repository.user_id != latest.user_id:
        raise CleanupSafetyError(
            "Checkpoint user does not match repository owner."
        )

    # Authenticate as the actual owner, never use service_role.
    identity = repository._request(
        "GET",
        "/auth/v1/user",
    )

    if (
        not isinstance(identity, dict)
        or identity.get("id") != latest.user_id
    ):
        raise CleanupSafetyError(
            "JWT does not belong to checkpoint owner."
        )

    # RLS-filtered existence check. An error must stop cleanup.
    rows = repository._request(
        "GET",
        "/rest/v1/exams",
        params={
            "select": "id",
            "id": f"eq.{latest.exam_id}",
            "limit": "1",
        },
    )

    if not isinstance(rows, list):
        raise CleanupSafetyError(
            "Cannot verify database exam status."
        )

    if rows:
        raise CleanupSafetyError(
            "Exam exists in PostgreSQL; Storage must be preserved."
        )

    # Delete only explicit object paths. No wildcards or bucket sweeps.
    for start in range(0, len(paths), 100):
        repository._remove_objects(
            paths[start:start + 100]
        )

    # Keep the checkpoint if the Storage deletion request fails.
    latest.path.unlink()

    return len(paths)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect and explicitly clean abandoned "
            "QuizPDF checkpoints."
        )
    )

    parser.add_argument(
        "--min-age-hours",
        type=int,
        default=48,
    )

    parser.add_argument(
        "--apply",
        metavar="EXAM_ID",
        help="Delete only the specified abandoned exam objects",
    )

    parser.add_argument(
        "--user-id",
        help="Owner UUID, required with --apply",
    )

    args = parser.parse_args()

    candidates = find_expired_checkpoints(
        min_age_hours=args.min_age_hours
    )

    print(
        f"Eligible local checkpoints: {len(candidates)}"
    )

    for item in candidates:
        print(
            f"exam={item.exam_id} "
            f"owner={item.user_id} "
            f"age_hours={item.age_hours:.1f}"
        )

    if args.apply is None:
        print(
            "READ ONLY. No Storage objects or "
            "local checkpoints were deleted."
        )
        return

    selected = [
        item
        for item in candidates
        if item.exam_id == args.apply
        and item.user_id == args.user_id
    ]

    if len(selected) != 1:
        parser.error(
            "No uniquely matching expired checkpoint. "
            "Use --user-id and the exact --apply EXAM_ID."
        )

    from backend.cloud_repository import CloudRepository

    try:
        from dotenv import load_dotenv
        load_dotenv(
            Path(__file__).resolve().parent / ".env"
        )
    except ImportError:
        pass

    token = getpass.getpass(
        "Supabase user access token (hidden): "
    )

    if not token:
        parser.error(
            "A user access token is required."
        )

    with CloudRepository(
        selected[0].user_id,
        token,
    ) as repository:
        count = cleanup_checkpoint(
            repository,
            selected[0],
            confirm_exam_id=args.apply,
            min_age_hours=args.min_age_hours,
        )

    print(
        "Cleanup request completed for "
        f"{count} exact object paths. "
        "Local checkpoint removed."
    )


if __name__ == "__main__":
    main()
