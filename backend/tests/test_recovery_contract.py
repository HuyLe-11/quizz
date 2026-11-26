import json
import unittest
from dataclasses import FrozenInstanceError, asdict, replace

from backend.engine.recovery_contract import (
    RecoveryEvidence,
    RecoveryProposal,
    RecoveryTarget,
    TextBlock,
)
from backend.engine.spatial_association import PixelFrame


class RecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.digest = "a" * 64
        self.target = RecoveryTarget(self.digest, "upload-question-0", 1, 0)
        self.frame = PixelFrame("page-1:original", 320, 160)
        self.ocr = RecoveryEvidence(
            self.digest, 1, "supplemental_ocr", "ocr-job-1",
            observed_text="người", confidence=.96,
        )

    def proposal(self, **changes):
        values = dict(
            proposal_id="proposal-1", target=self.target,
            operation="text_reconciliation", original=TextBlock("text", "ngườ i"),
            replacement=TextBlock("text", "người"), evidence=(self.ocr,),
        )
        values.update(changes)
        return RecoveryProposal(**values)

    def test_multisubject_text_preserved_exactly(self):
        for before, after in (
            ("destroy/ed", "destroyed"), ("ngườ i", "người"),
            ("H₂S0₄", "H₂SO₄"), ("9,8 m/s", "9,8 m/s²"),
            ("x2", "x²"), ("195 4", "1954"), ("O(n2)", "O(n²)"),
            ("a\u0301", "á"),
        ):
            with self.subTest(before=before):
                proposal = self.proposal(
                    original=TextBlock("text", before), replacement=TextBlock("text", after),
                    evidence=(replace(self.ocr, observed_text=after),),
                )
                self.assertEqual(proposal.original.value, before)
                self.assertEqual(proposal.replacement.value, after)

    def test_option_labels_are_not_fixed(self):
        for label in ("B", "E", "9", "ii", "đ"):
            with self.subTest(label=label):
                target = replace(self.target, option_label=label, block_index=2)
                self.assertEqual(self.proposal(target=target).target.option_label, label)
        self.assertIsNone(self.proposal().target.option_label)

    def test_blank_proposal_with_pixel_evidence(self):
        visual = RecoveryEvidence(
            self.digest, 1, "pdf_pixels", "original-render-1",
            frame=self.frame, bbox=(20, 40, 80, 43),
        )
        proposal = self.proposal(
            operation="blank_recovery", original=TextBlock("text", "x ="),
            replacement=TextBlock("text", "x = ________"), evidence=(visual,),
        )
        self.assertEqual(proposal.evidence[0].bbox, (20, 40, 80, 43))

    def test_format_only_change(self):
        proposal = self.proposal(
            operation="format_recovery", original=TextBlock("text", "x"),
            replacement=TextBlock("latex", r"$\underline{x}$"),
        )
        self.assertEqual(proposal.replacement.kind, "latex")

    def test_original_and_evidence_are_immutable(self):
        proposal = self.proposal()
        for obj, field, value in (
            (proposal, "operation", "blank_recovery"),
            (proposal.target, "block_index", 2),
            (proposal.original, "value", "changed"),
            (proposal.evidence[0], "confidence", 1),
        ):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, field, value)

    def test_wrong_document_or_page_rejected(self):
        for evidence in (replace(self.ocr, document_sha256="b" * 64),
                         replace(self.ocr, page_number=2)):
            with self.assertRaisesRegex(ValueError, "another document or page"):
                self.proposal(evidence=(evidence,))

    def test_evidence_required_and_immutable(self):
        for evidence in ((), [self.ocr], (None,)):
            with self.assertRaises(ValueError):
                self.proposal(evidence=evidence)

    def test_noop_rejected(self):
        with self.assertRaisesRegex(ValueError, "no change"):
            self.proposal(replacement=TextBlock("text", "ngườ i"))

    def test_invalid_target_rejected(self):
        for changes in ({"document_sha256": "abc"}, {"question_key": " "},
                        {"page_number": 0}, {"page_number": True},
                        {"block_index": -1}, {"option_label": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.target, **changes)

    def test_invalid_block_or_operation_rejected(self):
        for kind, value in (("image", "x"), ("text", ""), ("text", 3)):
            with self.assertRaises(ValueError):
                TextBlock(kind, value)
        with self.assertRaises(ValueError):
            self.proposal(operation="set_answer")

    def test_invalid_confidence_rejected(self):
        for confidence in (-.1, 1.1, float("nan"), float("inf"), True, "1"):
            with self.assertRaises(ValueError):
                replace(self.ocr, confidence=confidence)

    def test_invalid_geometry_rejected(self):
        for bbox in ((0, 0, 321, 20), (0, 0, 0, 20), (-1, 0, 20, 20),
                     (0, 0, float("nan"), 20), (0, 0, 20, True), [0, 0, 20, 20]):
            with self.assertRaises(ValueError):
                replace(self.ocr, frame=self.frame, bbox=bbox)
        with self.assertRaises(ValueError):
            replace(self.ocr, frame=self.frame)
        with self.assertRaises(ValueError):
            replace(self.ocr, bbox=(0, 0, 20, 20))
        with self.assertRaises(ValueError):
            replace(self.ocr, frame=PixelFrame("bad", 0, 160), bbox=(0, 0, 20, 20))

    def test_visual_geometry_and_observation_required(self):
        with self.assertRaises(ValueError):
            replace(self.ocr, source_kind="pdf_pixels")
        with self.assertRaises(ValueError):
            replace(self.ocr, observed_text=None)
        with self.assertRaises(ValueError):
            replace(self.ocr, source_kind="unknown")

    def test_json_serialization(self):
        result = json.loads(json.dumps(asdict(self.proposal()), ensure_ascii=False))
        self.assertEqual(result["replacement"]["value"], "người")
        self.assertEqual(result["evidence"][0]["source_id"], "ocr-job-1")
        self.assertNotIn("is_correct", result)

    def test_distinct_frames_are_not_claimed_registered(self):
        other = replace(self.ocr, frame=PixelFrame("ocr-unwarped", 320, 160),
                        bbox=(20, 30, 60, 50))
        visual = RecoveryEvidence(self.digest, 1, "pdf_pixels", "render",
                                  frame=self.frame, bbox=(20, 40, 80, 43))
        proposal = self.proposal(evidence=(other, visual))
        self.assertNotEqual(proposal.evidence[0].frame, proposal.evidence[1].frame)


if __name__ == "__main__":
    unittest.main()
