import copy
import unittest
from dataclasses import replace

from backend.engine.recovery_contract import (
    RecoveryEvidence, RecoveryProposal, RecoveryTarget, TextBlock,
)
from backend.engine.recovery_validator import review_proposals


class RecoveryValidatorTests(unittest.TestCase):
    def setUp(self):
        self.digest = "a" * 64
        self.questions = {"q-1": {
            "page_number": 1,
            "question_content": [{"type": "text", "value": "x ="}],
            "mcp_options": [{"label": "E", "is_correct": False,
                             "content": [{"type": "text", "value": "H₂S0₄"}]}],
        }}
        self.target = RecoveryTarget(self.digest, "q-1", 1, 0, "E")
        self.evidence = RecoveryEvidence(
            self.digest, 1, "supplemental_ocr", "job-1", observed_text="H₂SO₄",
        )
        self.proposal = RecoveryProposal(
            "p-1", self.target, "text_reconciliation",
            TextBlock("text", "H₂S0₄"), TextBlock("text", "H₂SO₄"),
            (self.evidence,),
        )

    def review(self, *proposals):
        return review_proposals(self.digest, self.questions, proposals)

    def decision(self, proposal=None):
        return self.review(self.proposal if proposal is None else proposal)[0]

    def test_valid_option_pending_not_authorized(self):
        result = self.decision()
        self.assertEqual(result.status, "pending_evidence")
        self.assertEqual(result.reason, "structure_valid")

    def test_stem_and_option_are_independent(self):
        stem = replace(self.proposal, proposal_id="p-2",
                       target=replace(self.target, option_label=None),
                       original=TextBlock("text", "x ="),
                       replacement=TextBlock("text", "x = ________"),
                       operation="blank_recovery")
        self.assertEqual([d.status for d in self.review(self.proposal, stem)],
                         ["pending_evidence", "pending_evidence"])

    def test_document_mismatch(self):
        result = review_proposals("b" * 64, self.questions, [self.proposal])[0]
        self.assertEqual(result.reason, "wrong_document")

    def test_question_missing(self):
        self.assertEqual(self.decision(replace(self.proposal,
            target=replace(self.target, question_key="unknown"))).reason,
            "question_unavailable")

    def test_wrong_page(self):
        self.questions["q-1"]["page_number"] = 2
        self.assertEqual(self.decision().reason, "wrong_page")

    def test_missing_and_duplicate_option(self):
        q = self.questions["q-1"]
        original = q["mcp_options"]
        for options in ([], original + copy.deepcopy(original)):
            q["mcp_options"] = options
            self.assertEqual(self.decision().reason, "option_not_unique")

    def test_missing_block(self):
        self.assertEqual(self.decision(replace(self.proposal,
            target=replace(self.target, block_index=1))).reason, "block_unavailable")

    def test_unsupported_block(self):
        self.questions["q-1"]["mcp_options"][0]["content"][0]["type"] = "image"
        self.assertEqual(self.decision().reason, "unsupported_block")

    def test_stale_text_or_kind(self):
        for block in ({"type": "text", "value": "changed"},
                      {"type": "latex", "value": "H₂S0₄"}):
            self.questions["q-1"]["mcp_options"][0]["content"] = [block]
            self.assertEqual(self.decision().reason, "stale_original")

    def test_conflicting_replacements(self):
        other = replace(self.proposal, proposal_id="p-2",
                        replacement=TextBlock("text", "H₂S₀₄"))
        self.assertEqual([d.status for d in self.review(self.proposal, other)],
                         ["conflict", "conflict"])

    def test_equivalent_replacements_still_conflict(self):
        other = replace(self.proposal, proposal_id="p-2")
        self.assertEqual([d.status for d in self.review(self.proposal, other)],
                         ["conflict", "conflict"])

    def test_duplicate_proposal_id(self):
        self.assertEqual([d.reason for d in self.review(self.proposal, self.proposal)],
                         ["duplicate_proposal_id", "duplicate_proposal_id"])

    def test_invalid_proposal_does_not_block_valid_one(self):
        stale = replace(self.proposal, proposal_id="p-2",
                        original=TextBlock("text", "outdated"))
        self.assertEqual([d.status for d in self.review(self.proposal, stale)],
                         ["pending_evidence", "rejected"])

    def test_aliasing_same_block_conflicts(self):
        self.questions["q-alias"] = self.questions["q-1"]
        other = replace(self.proposal, proposal_id="p-2",
                        target=replace(self.target, question_key="q-alias"))
        self.assertEqual([d.status for d in self.review(self.proposal, other)],
                         ["conflict", "conflict"])

    def test_read_only_and_deterministic(self):
        before = copy.deepcopy(self.questions)
        self.assertEqual(self.review(self.proposal), self.review(self.proposal))
        self.assertEqual(self.questions, before)
        self.assertEqual(self.review(), ())

    def test_invalid_inputs(self):
        for args in (("abc", self.questions, []),
                     (self.digest, [], []), (self.digest, self.questions, [None])):
            with self.assertRaises(ValueError):
                review_proposals(*args)


if __name__ == "__main__":
    unittest.main()
