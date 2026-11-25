"""Validate and merge review-only registered mark snapshots."""
from __future__ import annotations

import hashlib
import json
import math
import re


def _finite_number(value):

    if type(value) not in (int, float):
        return False

    try:
        return math.isfinite(value)

    except OverflowError:
        return False


def registered_evidence(exam, question, labels):

    """Return a normalized entry bound to the current question and source."""

    extra = exam.get("extra")

    if not isinstance(extra, dict):
        return None

    snapshot = extra.get("registered_marks")

    if (
        not isinstance(snapshot, dict)
        or type(snapshot.get("version")) is not int
        or snapshot["version"] != 1
        or snapshot.get("status") not in (
            "processed",
            "partial",
        )
    ):
        return None

    sha = exam.get("source_sha256")

    number = question.get(
        "source_question_number",
        question.get("question_id"),
    )

    page = question.get("page_number")

    ident = question.get("id")

    if (
        not isinstance(sha, str)
        or not re.fullmatch(r"[0-9a-f]{64}", sha)
        or question.get("type") != "mcp"
        or question.get("multiple_choice") is not False
        or type(number) is not int
        or number < 1
        or type(page) is not int
        or page < 1
        or not isinstance(ident, str)
        or not ident
        or not 2 <= len(labels) <= 8
        or any(
            l not in tuple("ABCDEFGH")
            for l in labels
        )
        or len(set(labels)) != len(labels)
    ):
        return None

    entries = snapshot.get("by_question")

    pages = snapshot.get("pages")

    if (
        not isinstance(entries, dict)
        or not isinstance(pages, dict)
    ):
        return None

    entry = entries.get(ident)

    page_state = pages.get(str(page))

    if (
        not isinstance(entry, dict)
        or not isinstance(page_state, dict)
        or page_state.get("status") != "processed"
        or type(entry.get("page_number")) is not int
        or entry["page_number"] != page
        or type(
            entry.get("source_question_number")
        ) is not int
        or entry["source_question_number"] != number
        or entry.get("labels") != list(labels)
    ):
        return None

    normalized = {
        "source_sha256": sha,
        "question_id": ident,
        "page_number": page,
        "source_question_number": number,
        "labels": list(labels),
    }

    if entry.get("status") == "conflict":

        return {
            **normalized,
            "status": "conflict",
        }

    if (
        entry.get("status") != "uncertain"
        or entry.get("detected_answer") not in labels
        or entry.get("registration_method") not in (
            "local_affine",
            "template",
        )
        or entry.get("evidence") not in (
            "leading_region",
            "repeated_columns",
        )
    ):
        return None

    box = entry.get("mark_coordinates")

    if (
        not isinstance(box, list)
        or len(box) != 4
        or any(
            not _finite_number(v)
            for v in box
        )
        or not (
            0 <= box[0] < box[2]
            and 0 <= box[1] < box[3]
        )
    ):
        return None

    return {
        **normalized,
        "status": "uncertain",
        "detected_answer": entry["detected_answer"],
        "mark_coordinates": [
            float(v)
            for v in box
        ],
        "registration_method": entry["registration_method"],
        "evidence": entry["evidence"],
    }


def review_fingerprint(
    legacy_fingerprint,
    evidence,
):

    if evidence is None:
        return legacy_fingerprint

    payload = {
        "merge_version": 1,
        "legacy": legacy_fingerprint,
        "registered": evidence,
    }

    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]


def merge_registered_evidence(
    detection,
    evidence,
    fingerprint,
):

    """Keep the old contract; conflicting evidence never supplies a label."""

    if evidence is None:
        return dict(detection)

    merged = {
        **detection,
        "config_fingerprint": fingerprint,
    }

    page = evidence["page_number"]

    label = evidence.get("detected_answer")

    old_label = detection.get("detected_answer")

    old_status = detection.get("status")

    compatible = (
        detection.get("page_number") == page
        and (
            (
                old_status == "not_found"
                and old_label is None
            )
            or (
                old_status in (
                    "detected",
                    "uncertain",
                )
                and old_label == label
            )
        )
    )

    if (
        evidence["status"] == "conflict"
        or not compatible
    ):

        return {
            **merged,
            "status": "uncertain",
            "detected_answer": None,
            "confidence": 0.0,
            "page_number": page,
            "mark_coordinates": None,
        }

    # Fixed review heuristic, not a calibrated probability.

    confidence = 0.60

    if old_status in ("detected", "uncertain"):

        old_score = detection.get("confidence")

        if _finite_number(old_score):

            confidence = min(
                confidence,
                max(0.0, old_score),
            )

    return {
        **merged,
        "status": "uncertain",
        "detected_answer": label,
        "confidence": confidence,
        "page_number": page,
        "mark_coordinates": evidence["mark_coordinates"],
    }