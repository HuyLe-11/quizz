import copy
import unittest

from backend.engine.ocr_inline_reconstruction import (
    propose_inline_reconstruction,
)


STEM = (
    r"Vietnamese Independence Day, which is celebrated annually  "
    r"$ \underline{\text{2nd}} $, holds great significance "
    r"for all Vietnamese people."
)


ROOT = {
    "rec_texts": [
        (
            "Question 13. Vietnamese Independence Day, "
            "which is celebrated annuallySeptember2nd, hold"
        ),
        "great significance for all Vietnamese people.",
        "B. in",
        "C. at",
        "D. from",
    ],
    "rec_scores": [
        .9916917681694031,
        .9970950484275818,
        .996,
        .996,
        .939,
    ],
}


class OCRInlineReconstructionTests(unittest.TestCase):

    def propose(
        self,
        stem=STEM,
        root=None,
        number=13,
    ):

        return propose_inline_reconstruction(
            stem,
            copy.deepcopy(
                ROOT if root is None else root
            ),
            number,
        )

    def test_real_question_reconstructs_missing_word(self):

        result = self.propose()

        self.assertIsNotNone(result)

        self.assertEqual(
            result["inserted_word"],
            "September",
        )

        self.assertEqual(
            result["suffix_difference"],
            {
                "primary": "holds",
                "secondary": "hold",
            },
        )

        self.assertIn(
            "annually September 2nd, holds",
            result["text_without_blank"],
        )

        self.assertTrue(
            result["requires_image_verification"]
        )

        self.assertNotIn(
            "_",
            result["text_without_blank"],
        )

    def test_generalizes_to_another_date(self):

        stem = STEM.replace(
            "2nd",
            "21st",
        )

        evidence = copy.deepcopy(ROOT)

        evidence["rec_texts"][0] = (
            evidence["rec_texts"][0].replace(
                "September2nd, hold",
                "October21st, holds",
            )
        )

        result = self.propose(
            stem=stem,
            root=evidence,
        )

        self.assertIsNotNone(result)

        self.assertEqual(
            result["inserted_word"],
            "October",
        )

        self.assertEqual(
            result["ordinal"],
            "21st",
        )

        self.assertIsNone(
            result["suffix_difference"]
        )

    def test_low_confidence_is_rejected(self):

        evidence = copy.deepcopy(ROOT)

        evidence["rec_scores"][0] = .80

        self.assertIsNone(
            self.propose(
                root=evidence
            )
        )

    def test_conflicting_prefix_is_rejected(self):

        evidence = copy.deepcopy(ROOT)

        evidence["rec_texts"][0] = (
            evidence["rec_texts"][0].replace(
                "Vietnamese Independence",
                "National Independence",
            )
        )

        self.assertIsNone(
            self.propose(
                root=evidence
            )
        )

    def test_conflicting_suffix_is_rejected(self):

        evidence = copy.deepcopy(ROOT)

        evidence["rec_texts"][1] = (
            "great significance for other people."
        )

        self.assertIsNone(
            self.propose(
                root=evidence
            )
        )

    def test_missing_word_evidence_is_rejected(self):

        evidence = copy.deepcopy(ROOT)

        evidence["rec_texts"][0] = (
            evidence["rec_texts"][0].replace(
                "September",
                "",
            )
        )

        self.assertIsNone(
            self.propose(
                root=evidence
            )
        )

    def test_duplicate_heading_is_rejected(self):

        evidence = copy.deepcopy(ROOT)

        evidence["rec_texts"].append(
            evidence["rec_texts"][0]
        )

        evidence["rec_scores"].append(
            .99
        )

        self.assertIsNone(
            self.propose(
                root=evidence
            )
        )

    def test_existing_blank_is_not_modified(self):

        stem = STEM.replace(
            "annually",
            "annually ___",
        )

        self.assertIsNone(
            self.propose(
                stem=stem
            )
        )

    def test_input_evidence_is_not_mutated(self):

        evidence = copy.deepcopy(ROOT)

        propose_inline_reconstruction(
            STEM,
            evidence,
            13,
        )

        self.assertEqual(
            evidence,
            ROOT,
        )


if __name__ == "__main__":
    unittest.main()