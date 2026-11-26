"""Read-only structural validation and conflict planning for recovery proposals."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from .recovery_contract import RecoveryProposal, TextBlock


@dataclass(frozen=True, slots=True)
class ProposalDecision:
    proposal: RecoveryProposal
    status: Literal["pending_evidence", "rejected", "conflict"]
    reason: str


def _resolve(proposal, document_sha256, questions):
    target = proposal.target
    if target.document_sha256 != document_sha256:
        return None, "wrong_document"

    question = questions.get(target.question_key)
    if not isinstance(question, dict):
        return None, "question_unavailable"
    if (type(question.get("page_number")) is not int
            or question["page_number"] != target.page_number):
        return None, "wrong_page"

    if target.option_label is None:
        blocks = question.get("question_content")
    else:
        options = question.get("mcp_options")
        if not isinstance(options, list) or not all(isinstance(o, dict) for o in options):
            return None, "invalid_options"
        matches = [o for o in options if o.get("label") == target.option_label]
        if len(matches) != 1:
            return None, "option_not_unique"
        blocks = matches[0].get("content")

    if not isinstance(blocks, list) or target.block_index >= len(blocks):
        return None, "block_unavailable"
    block = blocks[target.block_index]
    if not isinstance(block, dict):
        return None, "invalid_block"
    try:
        original = TextBlock(block.get("type"), block.get("value"))
    except ValueError:
        return None, "unsupported_block"
    if original != proposal.original:
        return None, "stale_original"
    return block, ""


def review_proposals(
    document_sha256: str,
    questions_by_key: Mapping[str, dict],
    proposals: Sequence[RecoveryProposal],
) -> tuple[ProposalDecision, ...]:
    """Plan one immutable batch against one upload-local snapshot.

    The caller supplies a unique question-key mapping for this upload.
    A pending decision is NOT authorization to apply a replacement.
    All competing structurally valid proposals for one block conflict,
    including equivalent replacements. No input is mutated.
    """
    if not isinstance(document_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", document_sha256):
        raise ValueError("Invalid document SHA-256.")
    if not isinstance(questions_by_key, Mapping):
        raise ValueError("Expected a question-key mapping.")
    if not isinstance(proposals, Sequence) or not all(
        isinstance(p, RecoveryProposal) for p in proposals
    ):
        raise ValueError("Expected recovery proposals.")

    ids = Counter(p.proposal_id for p in proposals)
    resolved = []
    for p in proposals:
        if ids[p.proposal_id] != 1:
            resolved.append((p, None, "duplicate_proposal_id"))
            continue
        block, reason = _resolve(p, document_sha256, questions_by_key)
        resolved.append((p, block, reason))

    # Object identity also catches aliasing of one mutable block in two paths.
    targets = Counter(id(block) for _, block, _ in resolved if block is not None)
    decisions = []
    for p, block, reason in resolved:
        if block is None:
            decisions.append(ProposalDecision(p, "rejected", reason))
        elif targets[id(block)] > 1:
            decisions.append(ProposalDecision(p, "conflict", "competing_block_proposals"))
        else:
            decisions.append(ProposalDecision(p, "pending_evidence", "structure_valid"))
    return tuple(decisions)
