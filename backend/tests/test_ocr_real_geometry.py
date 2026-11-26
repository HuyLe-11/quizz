import copy
import tempfile
import unittest

from pathlib import Path

import cv2
import numpy as np
import pymupdf

from backend.engine.ocr_blank_recovery import (
    recover_scan_blanks,
    _split,
)

from backend.engine.ocr_text_reconciliation import (
    _row,
    _split_repairs,
    _suffix_repairs,
)


def root(entries):
    return {
        "rec_texts": [e[0] for e in entries],
        "rec_boxes": [e[1] for e in entries],
        "rec_scores": [e[2] for e in entries],
    }


def options(*values):
    return [
        {
            "label": label,
            "content": [
                {
                    "type": kind,
                    "value": value,
                }
            ],
        }
        for label, (kind, value) in zip(
            "ABCD",
            values,
        )
    ]


Q7_STEM = (
    r"A fire-fighter was injured severely  "
    r"$ \underline{\text{while he}} $ to put out a fire."
)


Q7_ROOT = root([
    (
        "Question 7. A fire-fighter was injured severely while he",
        [0, 550, 538, 583],
        .997220695,
    ),
    (
        "to put out a fire.",
        [651, 547, 819, 577],
        .987146676,
    ),
    (
        "B. helped",
        [289, 581, 386, 610],
        .999405205,
    ),
    (
        "C. has helped",
        [516, 579, 655, 606],
        .999964774,
    ),
    (
        "D. helps",
        [732, 576, 827, 605],
        .990821003,
    ),
    (
        "A.vas helping 4",
        [34, 583, 224, 614],
        .931734264,
    ),
])


Q14_ROOT = root([
    (
        "Question 14. I",
        [0, 1004, 149, 1036],
        .97666055,
    ),
    (
        "my best friend again since I went abroad five years ago.",
        [248, 1008, 798, 1038],
        .990552127,
    ),
    (
        "A. don't meet",
        [42, 1036, 180, 1067],
        .977999687,
    ),
])


class OCRRealGeometryTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()

        self.addCleanup(temp.cleanup)

        self.path = (
            Path(temp.name) / "scan.pdf"
        )

    def make_pdf(
        self,
        line=None,
        *,
        occupied=False,
    ):
        image = np.full(
            (1310, 959),
            255,
            dtype=np.uint8,
        )

        if line:
            x0, x1, y = line

            cv2.line(
                image,
                (x0, y),
                (x1, y),
                0,
                2,
            )

            if occupied:
                cv2.rectangle(
                    image,
                    (x0 + 12, y - 16),
                    (x1 - 12, y - 5),
                    0,
                    -1,
                )

        ok, png = cv2.imencode(
            ".png",
            image,
        )

        self.assertTrue(ok)

        with pymupdf.open() as doc:

            page = doc.new_page(
                width=959,
                height=1310,
            )

            page.insert_image(
                page.rect,
                stream=png.tobytes(),
            )

            doc.save(self.path)

    def run_case(
        self,
        *,
        n=7,
        stem=Q7_STEM,
        kind="latex",
        evidence=None,
        region=None,
    ):
        evidence = copy.deepcopy(
            Q7_ROOT
            if evidence is None
            else evidence
        )

        region = (
            [0, 540, 819, 610]
            if region is None
            else region
        )

        instruction = (
            "Choose the letter A, B, C, or D "
            "to complete each question."
        )

        choices = options(
            *[
                ("text", v)
                for v in (
                    "was helping",
                    "helped",
                    "has helped",
                    "helps",
                )
            ]
        )

        q = {
            "page_number": 1,
            "source_question_number": n,
            "type": "mcp",
            "multiple_choice": False,
            "section_instruction": instruction,
            "question_content": [
                {
                    "type": "text",
                    "value": instruction,
                },
                {
                    "type": kind,
                    "value": stem,
                },
            ],
            "mcp_options": choices,
            "correct_answer": "A",
        }

        primary = {
            "pruned_result": {
                "width": 959,
                "height": 1310,
                "parsing_res_list": [
                    {
                        "block_content": (
                            f"Question {n}. {stem}\n"
                            "A. option B. option C. option D. option"
                        ),
                        "block_bbox": region,
                    }
                ],
            }
        }

        secondary = {
            "pruned_result": evidence,
        }

        before = copy.deepcopy(q)

        repairs = recover_scan_blanks(
            self.path,
            [primary],
            [q],
            geometry_page_for=lambda _: secondary,
        )

        self.assertEqual(
            q["mcp_options"],
            before["mcp_options"],
        )

        self.assertEqual(
            q["correct_answer"],
            before["correct_answer"],
        )

        self.assertEqual(
            q["question_content"][0],
            before["question_content"][0],
        )

        if not repairs:
            self.assertEqual(
                q,
                before,
            )

        return repairs, q

    def test_q2_real_row_repairs_only_supported_options(self):
        data = root([
            (
                "Question 2. A. destroyed",
                [0, 293, 253, 339],
                .980261624,
            ),
            (
                "B. performed",
                [282, 308, 409, 333],
                .998971879,
            ),
            (
                "C.vanted 1",
                [485, 297, 631, 335],
                .863156021,
            ),
            (
                "D.combined",
                [712, 306, 842, 330],
                .999788761,
            ),
        ])

        row = _row(
            data,
            2,
            list("ABCD"),
        )

        self.assertIsNotNone(row)

        choices = options(
            *[
                ("text", v)
                for v in (
                    "destroy/ed",
                    "perform/ed",
                    "wanted",
                    "combined",
                )
            ]
        )

        self.assertEqual(
            _split_repairs(choices, row),
            ["A", "B"],
        )

        self.assertEqual(
            [
                c["content"][0]["value"]
                for c in choices
            ],
            [
                "destroyed",
                "performed",
                "wanted",
                "combined",
            ],
        )

    def test_q4_real_row_keeps_only_observed_underlines(self):
        data = root([
            (
                "Question 4A.parks 2",
                [0, 352, 236, 393],
                .972647011,
            ),
            (
                "B. conditions",
                [281, 363, 409, 388],
                .995411992,
            ),
            (
                "C. systems",
                [499, 359, 608, 383],
                .997709632,
            ),
            (
                "D. letters",
                [717, 356, 815, 385],
                .996223807,
            ),
        ])

        row = _row(
            data,
            4,
            list("ABCD"),
        )

        self.assertIsNotNone(row)

        choices = options(
            ("latex", r"park $ \underline{s} $ 2"),
            ("text", "conditions"),
            ("text", "system s\u0332"),
            ("text", "letters"),
        )

        q = {}

        self.assertEqual(
            _suffix_repairs(q, choices, row),
            ["A", "C"],
        )

        self.assertEqual(
            [
                c["content"][0]["value"]
                for c in choices
            ],
            [
                "parks\u0332",
                "conditions",
                "systems\u0332",
                "letters",
            ],
        )

        self.assertIn(
            "2",
            q["parser_warning"],
        )

    def test_q7_gap_exceeds_legacy_limit(self):
        self.assertIsNone(
            _split(Q7_ROOT, 7)
        )

        left, right, start, end = _split(
            Q7_ROOT,
            7,
            marked=True,
        )

        self.assertTrue(
            left.endswith("while he")
        )

        self.assertEqual(
            right,
            "to put out a fire.",
        )

        self.assertAlmostEqual(
            start,
            538 / 819,
        )

        self.assertAlmostEqual(
            end,
            651 / 819,
        )

    def test_q7_recovers_only_with_printed_rule(self):
        self.make_pdf(
            (538, 651, 575)
        )

        repairs, q = self.run_case()

        self.assertEqual(
            repairs,
            [
                {
                    "page_number": 1,
                    "source_question_number": 7,
                }
            ],
        )

        self.assertEqual(
            q["question_content"][-1],
            {
                "type": "text",
                "value": (
                    "A fire-fighter was injured severely while he "
                    "________ to put out a fire."
                ),
            },
        )

        self.assertTrue(
            q["requires_source_image"]
        )

        self.assertTrue(
            q["parse_fallback"]
        )

        self.assertIn(
            "gạch chân",
            q["parser_warning"],
        )

    def test_q7_no_printed_rule_no_repair(self):
        self.make_pdf()

        self.assertEqual(
            self.run_case()[0],
            [],
        )

    def test_q7_ink_above_rule_rejects_repair(self):
        self.make_pdf(
            (538, 651, 575),
            occupied=True,
        )

        self.assertEqual(
            self.run_case()[0],
            [],
        )

    def test_q7_wrong_rule_location_rejects_repair(self):
        self.make_pdf(
            (300, 413, 575)
        )

        self.assertEqual(
            self.run_case()[0],
            [],
        )

    def test_q7_low_confidence_rejects_repair(self):
        self.make_pdf(
            (538, 651, 575)
        )

        evidence = copy.deepcopy(
            Q7_ROOT
        )

        evidence["rec_scores"][1] = .80

        self.assertEqual(
            self.run_case(
                evidence=evidence
            )[0],
            [],
        )

    def test_q7_different_text_rejects_repair(self):
        self.make_pdf(
            (538, 651, 575)
        )

        self.assertEqual(
            self.run_case(
                stem=Q7_STEM.replace(
                    "while he",
                    "while she",
                )
            )[0],
            [],
        )

    def test_q7_other_latex_rejects_repair(self):
        self.make_pdf(
            (538, 651, 575)
        )

        self.assertEqual(
            self.run_case(
                stem=Q7_STEM + r" $x^2$"
            )[0],
            [],
        )

    def test_q7_transformed_geometry_rejects_repair(self):
        self.make_pdf(
            (538, 651, 575)
        )

        evidence = copy.deepcopy(
            Q7_ROOT
        )

        evidence["doc_preprocessor_res"] = {
            "angle": 90,
        }

        self.assertEqual(
            self.run_case(
                evidence=evidence
            )[0],
            [],
        )

    def test_q14_legacy_repair_still_works(self):
        self.make_pdf(
            (149, 248, 1030)
        )

        repairs, q = self.run_case(
            n=14,
            stem=(
                "I. my best friend again since "
                "I went abroad five years ago."
            ),
            kind="text",
            evidence=Q14_ROOT,
            region=[0, 995, 798, 1065],
        )

        self.assertEqual(
            len(repairs),
            1,
        )

        self.assertEqual(
            q["question_content"][-1]["value"],
            (
                "I ________ my best friend again since "
                "I went abroad five years ago."
            ),
        )


if __name__ == "__main__":
    unittest.main()