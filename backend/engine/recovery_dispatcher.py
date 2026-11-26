"""Dispatch trusted evidence checks and apply verified block edits to a copy."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from .recovery_contract import RecoveryProposal, TextBlock
from .recovery_validator import _resolve, review_proposals


REVIEW_WARNING = (
    "Nội dung đã được phục hồi; cần đối chiếu với tài liệu gốc."
)


@dataclass(frozen=True, slots=True)
class EvidenceVerdict:
    proposal: RecoveryProposal
    status: Literal["verified", "uncertain", "rejected"]
    reason: str
    replacement: TextBlock | None = None

    def __post_init__(self):
        if not isinstance(self.proposal, RecoveryProposal):
            raise ValueError("Invalid verdict proposal.")
        if self.status not in ("verified", "uncertain", "rejected"):
            raise ValueError("Invalid verdict status.")
        if (not isinstance(self.reason, str)
                or not self.reason.strip() or len(self.reason) > 256):
            raise ValueError("Invalid verdict reason.")
        if self.status == "verified":
            if not isinstance(self.replacement, TextBlock):
                raise ValueError("Verified verdict requires a replacement.")
        elif self.replacement is not None:
            raise ValueError("Unverified verdict cannot authorize a replacement.")


Verifier = Callable[[RecoveryProposal], EvidenceVerdict]


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    proposal: RecoveryProposal
    status: Literal["applied", "pending_evidence", "rejected", "conflict"]
    reason: str


@dataclass(slots=True)
class RecoveryBatchResult:
    questions: dict[str, dict]
    outcomes: tuple[RecoveryOutcome, ...]


def _apply_block(questions, proposal, block):
    """Copy the target path, so shared lists or blocks cannot leak edits."""
    t = proposal.target
    question = dict(questions[t.question_key])
    warning = question.get("parser_warning")
    if warning is not None and not isinstance(warning, str):
        return False

    updated = dict(block)
    updated["type"] = proposal.replacement.kind
    updated["value"] = proposal.replacement.value

    if t.option_label is None:
        blocks = list(question["question_content"])
        blocks[t.block_index] = updated
        question["question_content"] = blocks
    else:
        options = list(question["mcp_options"])
        index = next(i for i, o in enumerate(options)
                     if o.get("label") == t.option_label)
        option = dict(options[index])
        blocks = list(option["content"])
        blocks[t.block_index] = updated
        option["content"] = blocks
        options[index] = option
        question["mcp_options"] = options

    question["parse_fallback"] = True
    question["requires_source_image"] = True
    if REVIEW_WARNING not in (warning or ""):
        question["parser_warning"] = " ".join(
            part for part in (warning, REVIEW_WARNING) if part
        )
    questions[t.question_key] = question
    return True


def dispatch_recovery(
    document_sha256: str,
    questions_by_key: Mapping[str, dict],
    proposals: Sequence[RecoveryProposal],
    *,
    verifiers: Mapping[str, Verifier] | None = None,
) -> RecoveryBatchResult:
    """Return a detached snapshot and per-proposal outcomes.

    Verifiers are trusted application code bound to this upload's actual
    source evidence. They must independently verify the target and the full
    replacement; proposal metadata alone is insufficient. Never accept
    verifier implementations from OCR payloads or external requests.
    No verifier is registered by default. No input or persistent data is edited.
    """
    if not isinstance(questions_by_key, Mapping):
        raise ValueError("Expected a question-key mapping.")
    if verifiers is None:
        verifiers = {}
    if not isinstance(verifiers, Mapping) or not all(
        callable(v) for v in verifiers.values()
    ):
        raise ValueError("Expected a trusted verifier mapping.")

    registry = dict(verifiers)
    working = deepcopy(dict(questions_by_key))
    decisions = review_proposals(document_sha256, working, proposals)
    outcomes = []

    for decision in decisions:
        p = decision.proposal
        status, reason = decision.status, decision.reason
        if status == "pending_evidence":
            verifier = registry.get(p.operation)
            if verifier is None:
                reason = "verifier_unavailable"
            else:
                try:
                    verdict = verifier(p)
                except Exception:
                    status, reason = "rejected", "verifier_error"
                else:
                    if not isinstance(verdict, EvidenceVerdict) or verdict.proposal != p:
                        status, reason = "rejected", "invalid_verdict"
                    elif verdict.status == "uncertain":
                        reason = verdict.reason
                    elif verdict.status == "rejected":
                        status, reason = "rejected", verdict.reason
                    elif verdict.replacement != p.replacement:
                        status, reason = "rejected", "replacement_not_verified"
                    else:
                        block, failure = _resolve(p, document_sha256, working)
                        if block is None:
                            status, reason = "rejected", failure
                        elif not _apply_block(working, p, block):
                            status, reason = "rejected", "invalid_review_metadata"
                        else:
                            status, reason = "applied", verdict.reason
        outcomes.append(RecoveryOutcome(p, status, reason))

    return RecoveryBatchResult(working, tuple(outcomes))
