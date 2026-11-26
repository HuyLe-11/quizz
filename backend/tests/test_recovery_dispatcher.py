import copy
import unittest
from dataclasses import replace
from unittest.mock import Mock

from backend.engine.recovery_contract import (
    RecoveryEvidence, RecoveryProposal, RecoveryTarget, TextBlock,
)
from backend.engine.recovery_dispatcher import (
    EvidenceVerdict, REVIEW_WARNING, dispatch_recovery,
)


class RecoveryDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.digest = "a" * 64
        self.questions = {"q": {
            "page_number": 1,
            "question_content": [{"type": "text", "value": "x ="}],
            "mcp_options": [{"label": "E", "is_correct": False,
                             "content": [{"type": "text", "value": "H₂S0₄"}]}],
            "fill_in_answers": [], "match_pairs": [],
            "parser_warning": "Existing warning.",
        }}
        self.p = RecoveryProposal(
            "p", RecoveryTarget(self.digest, "q", 1, 0, "E"),
            "text_reconciliation", TextBlock("text", "H₂S0₄"),
            TextBlock("text", "H₂SO₄"),
            (RecoveryEvidence(self.digest, 1, "supplemental_ocr", "job",
                              observed_text="H₂SO₄", confidence=.99),),
        )

    def verified(self, p):
        # Test double for trusted verification, not a production policy.
        return EvidenceVerdict(p, "verified", "test_verified", p.replacement)

    def dispatch(self, *proposals, verifier=None):
        registry = {} if verifier is None else {"text_reconciliation": verifier}
        return dispatch_recovery(self.digest, self.questions, proposals,
                                 verifiers=registry)

    def test_no_verifier_even_with_high_confidence(self):
        result = self.dispatch(self.p)
        self.assertEqual(result.outcomes[0].status, "pending_evidence")
        self.assertEqual(result.questions, self.questions)
        self.assertIsNot(result.questions["q"], self.questions["q"])

    def test_verified_replacement_and_review_flags(self):
        before = copy.deepcopy(self.questions)
        result = self.dispatch(self.p, verifier=self.verified)
        q = result.questions["q"]
        self.assertEqual(result.outcomes[0].status, "applied")
        self.assertEqual(q["mcp_options"][0]["content"][0]["value"], "H₂SO₄")
        self.assertTrue(q["requires_source_image"])
        self.assertTrue(q["parse_fallback"])
        self.assertEqual(q["parser_warning"], "Existing warning. " + REVIEW_WARNING)
        self.assertFalse(q["mcp_options"][0]["is_correct"])
        self.assertEqual(q["fill_in_answers"], [])
        self.assertEqual(q["match_pairs"], [])
        self.assertEqual(q["question_content"], before["q"]["question_content"])
        self.assertEqual(self.questions, before)

    def test_uncertain_or_rejected(self):
        for status, expected in (("uncertain", "pending_evidence"),
                                 ("rejected", "rejected")):
            result = self.dispatch(self.p, verifier=lambda p: EvidenceVerdict(p, status, "test"))
            self.assertEqual(result.outcomes[0].status, expected)
            self.assertEqual(result.questions, self.questions)

    def test_verifier_exception(self):
        result = self.dispatch(self.p, verifier=Mock(side_effect=RuntimeError("secret")))
        self.assertEqual(result.outcomes[0].reason, "verifier_error")
        self.assertEqual(result.questions, self.questions)

    def test_invalid_verdict(self):
        result = self.dispatch(self.p, verifier=lambda p: True)
        self.assertEqual(result.outcomes[0].reason, "invalid_verdict")
        self.assertEqual(result.questions, self.questions)

    def test_verdict_bound_to_proposal(self):
        other = replace(self.p, proposal_id="other")
        result = self.dispatch(self.p, verifier=lambda p: self.verified(other))
        self.assertEqual(result.outcomes[0].reason, "invalid_verdict")
        self.assertEqual(result.questions, self.questions)

    def test_different_replacement_rejected(self):
        result = self.dispatch(self.p, verifier=lambda p: EvidenceVerdict(
            p, "verified", "test", TextBlock("text", "H₂S₀₄")))
        self.assertEqual(result.outcomes[0].reason, "replacement_not_verified")
        self.assertEqual(result.questions, self.questions)

    def test_conflicts_do_not_call_verifier(self):
        verifier = Mock(side_effect=self.verified)
        result = self.dispatch(self.p, replace(self.p, proposal_id="other"),
                               verifier=verifier)
        self.assertEqual([o.status for o in result.outcomes], ["conflict", "conflict"])
        verifier.assert_not_called()

    def test_stale_proposal_does_not_call_verifier(self):
        verifier = Mock(side_effect=self.verified)
        stale = replace(self.p, original=TextBlock("text", "stale"))
        result = self.dispatch(stale, verifier=verifier)
        self.assertEqual(result.outcomes[0].reason, "stale_original")
        verifier.assert_not_called()

    def test_independent_edits_and_warning_not_duplicated(self):
        stem = replace(self.p, proposal_id="stem", target=replace(self.p.target, option_label=None),
                       original=TextBlock("text", "x ="), replacement=TextBlock("text", "x = 2"))
        result = self.dispatch(self.p, stem, verifier=self.verified)
        self.assertEqual([o.status for o in result.outcomes], ["applied", "applied"])
        q = result.questions["q"]
        self.assertEqual(q["question_content"][0]["value"], "x = 2")
        self.assertEqual(q["parser_warning"].count(REVIEW_WARNING), 1)

    def test_shared_content_path_does_not_change_other_fields(self):
        q = self.questions["q"]
        q["explanation_content"] = q["mcp_options"][0]["content"]
        result = self.dispatch(self.p, verifier=self.verified)
        self.assertEqual(result.questions["q"]["explanation_content"][0]["value"], "H₂S0₄")
        self.assertEqual(result.questions["q"]["mcp_options"][0]["content"][0]["value"], "H₂SO₄")

    def test_repeat_application_is_stale(self):
        first = self.dispatch(self.p, verifier=self.verified)
        result = dispatch_recovery(self.digest, first.questions, [self.p],
                                   verifiers={"text_reconciliation": self.verified})
        self.assertEqual(result.outcomes[0].reason, "stale_original")
        self.assertEqual(result.questions, first.questions)

    def test_invalid_review_metadata_rejected(self):
        self.questions["q"]["parser_warning"] = []
        result = self.dispatch(self.p, verifier=self.verified)
        self.assertEqual(result.outcomes[0].reason, "invalid_review_metadata")
        self.assertEqual(result.questions, self.questions)

    def test_invalid_verdict_construction(self):
        for status, block in (("verified", None), ("uncertain", self.p.replacement),
                              ("rejected", self.p.replacement)):
            with self.assertRaises(ValueError):
                EvidenceVerdict(self.p, status, "test", block)

    def test_invalid_registry(self):
        with self.assertRaises(ValueError):
            dispatch_recovery(self.digest, self.questions, [self.p], verifiers={"text_reconciliation": True})


if __name__ == "__main__":
    unittest.main()
