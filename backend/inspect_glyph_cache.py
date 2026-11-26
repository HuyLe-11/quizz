"""Read-only local diagnostic: inventory geometry fields without OCR text or images."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .engine.ocr_job_journal import DEFAULT_PATH, MAX_RESULT_BYTES


INTEREST = re.compile(r"char|glyph|word|token|box|poly|coord|rec_|layout|unwarp|angle|orient|width|height|model_settings", re.I)


def _shape(value):
    parts = []
    for _ in range(6):
        if not isinstance(value, list):
            parts.append(type(value).__name__)
            break
        parts.append(f"list[{len(value)}]")
        if not value:
            break
        value = value[0]
    return "/".join(parts)


def geometry_inventory(page):
    """Field paths and shapes only; names do not establish coordinate semantics."""
    fields = {}
    stack = [("page", page, 0)]
    visited = 0
    truncated = False
    while stack:
        path, obj, depth = stack.pop()
        visited += 1
        if visited > 50_000:
            truncated = True
            break
        if depth > 16:
            truncated = True
            continue
        if isinstance(obj, dict):
            for key, value in obj.items():
                if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", key):
                    continue
                name = f"{path}.{key}"
                if INTEREST.search(key):
                    entry = fields.setdefault(name, {"occurrences": 0, "shapes": set()})
                    entry["occurrences"] += 1
                    if len(entry["shapes"]) < 20:
                        entry["shapes"].add(_shape(value))
                if isinstance(value, (dict, list)):
                    stack.append((name, value, depth + 1))
        elif isinstance(obj, list):
            for value in obj:
                if isinstance(value, (dict, list)):
                    stack.append((path + "[]", value, depth + 1))
    return {"truncated": truncated, "fields": [
        {"path": key, "occurrences": value["occurrences"], "shapes": sorted(value["shapes"])}
        for key, value in sorted(fields.items())
    ]}


def inspect_cached_job(path, scope, job_id):
    path = Path(path).resolve()
    if not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", scope):
        raise ValueError("Invalid journal path or scope.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
        raise ValueError("Invalid job ID.")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute("""
            SELECT r.payload, r.sha256
            FROM ocr_jobs j JOIN ocr_results r
              ON j.scope=r.scope AND j.batch_key=r.batch_key
             AND j.claim_id=r.claim_id AND j.job_id=r.job_id
            WHERE j.scope=? AND j.job_id=? AND j.state='completed'
              AND typeof(r.payload)='blob' AND length(r.payload)<=?
            """, (scope, job_id, MAX_RESULT_BYTES)).fetchall()
    if len(rows) != 1:
        raise RuntimeError("Expected one matching completed cache.")
    payload, digest = rows[0]
    if hashlib.sha256(payload).hexdigest() != digest:
        raise RuntimeError("Cache checksum mismatch.")
    data = json.loads(payload)
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("pages"), list) or not data["pages"]
            or not all(isinstance(p, dict) for p in data["pages"])
            or not isinstance(data.get("data_info"), dict)):
        raise RuntimeError("Invalid cache structure.")
    return {"job_id": job_id, "pages": [
        {"batch_page_index": i, **geometry_inventory(page)}
        for i, page in enumerate(data["pages"], 1)
    ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--job-id", action="append", required=True)
    args = parser.parse_args()
    results = [inspect_cached_job(args.journal, args.scope, job_id) for job_id in args.job_id]
    print(json.dumps(results, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
