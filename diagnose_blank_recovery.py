import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pymupdf

from backend.engine.ocr_job_journal import DEFAULT_PATH
from backend.engine.ocr_blank_recovery import (
    HEADING,
    _text,
    _marked_parts,
    _geometry_mode,
    _split,
    _region,
    _has_rule,
    recover_scan_blanks,
)

SCOPE = "84cc21e20b6ca202d92b0844429ed315625c334c2cba5e362e0e55eda4927ba7"

pdf = Path(
    input("Enter original PDF path: ").strip().strip('"')
).resolve()

if not pdf.is_file():
    raise SystemExit("PDF file not found.")


def load_page(conn, job_id):
    rows = conn.execute(
        """
        SELECT r.payload, r.sha256
        FROM ocr_jobs j
        JOIN ocr_results r
          ON r.scope = j.scope
         AND r.batch_key = j.batch_key
         AND r.claim_id = j.claim_id
         AND r.job_id = j.job_id
        WHERE j.scope = ?
          AND j.job_id = ?
          AND j.state = 'completed'
        """,
        (SCOPE, job_id),
    ).fetchall()

    if len(rows) != 1:
        raise RuntimeError("Missing or ambiguous cache.")

    payload, checksum = rows[0]

    if hashlib.sha256(payload).hexdigest() != checksum:
        raise RuntimeError("Invalid cache checksum.")

    data = json.loads(payload)

    if data.get("version") != 1 or len(data["pages"]) != 1:
        raise RuntimeError("Invalid cache structure.")

    return data["pages"][0]


with closing(
    sqlite3.connect(
        DEFAULT_PATH.resolve().as_uri() + "?mode=ro",
        uri=True,
    )
) as conn:

    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")

    primary = load_page(conn, "119293255782260736")
    secondary = load_page(conn, "119297800680390656")

    conn.rollback()


questions = []
blocks = primary["pruned_result"]["parsing_res_list"]
root = secondary["pruned_result"]

mode = _geometry_mode(secondary, root)

print("SUPPLEMENTAL GEOMETRY MODE:", mode)

with pymupdf.open(pdf) as doc:

    if len(doc) != 1:
        raise RuntimeError("Expected one-page PDF.")

    page = doc[0]

    print("PDF:", pdf)
    print("Dimensions:", tuple(page.rect))
    print("Native text:", bool(page.get_text().strip()))

    for number in (7, 13, 14):

        matches = []

        for block in blocks:

            lines = block.get("block_content", "").splitlines()

            if not lines:
                continue

            heading = HEADING.match(lines[0])

            if heading and int(heading[1]) == number:
                matches.append(heading[2])

        if len(matches) != 1:
            print("Missing stem:", number)
            continue

        stem = matches[0]
        marked = r"\underline" in stem

        split = _split(
            root,
            number,
            marked=marked,
        )

        rect = _region(
            page,
            primary,
            number,
            _text(stem),
        )

        print("\n" + "=" * 60)
        print("QUESTION:", number)
        print("STEM:", stem)
        print("MARKED:", marked)
        print("MARKED PARTS:", _marked_parts(stem) if marked else None)
        print("SPLIT:", split)
        print("REGION:", tuple(rect) if rect else None)

        if split is not None and rect is not None:
            print(
                "PRINTED RULE:",
                _has_rule(page, rect, split[2], split[3]),
            )

            if marked and mode == "unwarped_hint":
                print(
                    "UNIQUE PRINTED RULE:",
                    _has_rule(
                        page,
                        rect,
                        split[2],
                        split[3],
                        unique_in_region=True,
                    ),
                )

        questions.append({
            "page_number": 1,
            "source_question_number": number,
            "type": "mcp",
            "multiple_choice": False,
            "section_instruction": "Complete each question.",
            "question_content": [
                {
                    "type": "latex" if marked else "text",
                    "value": stem,
                }
            ],
            "mcp_options": [
                {"label": label}
                for label in "ABCD"
            ],
        })


repairs = recover_scan_blanks(
    pdf,
    [primary],
    questions,
    geometry_page_for=lambda _: secondary,
)

print("\n" + "=" * 60)
print("REPAIRS:", json.dumps(repairs, ensure_ascii=False))

for q in questions:

    print(json.dumps({
        "question": q["source_question_number"],
        "content": q["question_content"],
        "warning": q.get("parser_warning"),
    }, ensure_ascii=False))

print("\nDIAGNOSTIC COMPLETED")
print("Database writes: 0")
print("Provider requests: 0")