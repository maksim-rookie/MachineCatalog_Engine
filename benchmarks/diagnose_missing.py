#!/usr/bin/env python3
"""Compare a photo-derived benchmark with current local facts and OCR review.

This does NOT fill or correct facts. It only links each missing benchmark entry
with likely source rows in NEEDS_REVIEW.json, so that a human can inspect the PDF.

Usage:
  python benchmarks/diagnose_missing.py benchmarks/expected_photo_160.csv \
      output/ALL_MODELS_long.csv output/NEEDS_REVIEW.json

Output: next to ALL_MODELS_long.csv, MISSING_REFERENCE_AUDIT.csv
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def model_in_context(model: str, item: dict) -> bool:
    if item.get("model") == model:
        return True
    context = re.split(r"[,;]", str(item.get("models_context", "")))
    return model in {value.strip() for value in context}


def grade_candidate(expected: dict, item: dict) -> int:
    model = expected["model"].strip()
    parameter = expected["parameter"].strip()
    if not model_in_context(model, item):
        return 0
    exact_key = item.get("parameter") == parameter
    suggested = item.get("suggested_parameter") == parameter
    # These narrow aliases provide a search route only, not an automatic fact.
    alias = (parameter == "table_load_kg" and
             item.get("parameter") == "tool_capacity" and
             item.get("reason") == "tool_capacity_unit_conflict")
    if not (exact_key or suggested or alias):
        return 0
    score = 10 + (30 if exact_key else 25)
    if item.get("model") == model:
        score += 15
    try:
        if int(item.get("source_page", -1)) == int(expected.get("reference_page", -2)):
            score += 15
    except (ValueError, TypeError):
        pass
    if item.get("reason") in {
        "missing_model_value", "tool_capacity_unit_conflict",
        "empty_model_cell_ocr_unresolved", "spindle_taper_ocr_uncertain",
    }:
        score += 10
    return score


def make_audit(expected: list[dict], observed: list[dict], review: list[dict]) -> list[dict]:
    present = {(x.get("model", "").strip(), x.get("parameter", "").strip(),
                x.get("condition", "").strip()) for x in observed}
    result: list[dict] = []
    for expected_row in expected:
        model = expected_row["model"].strip()
        parameter = expected_row["parameter"].strip()
        condition = expected_row.get("condition", "").strip()
        if (model, parameter, condition) in present:
            continue
        matched = sorted(((grade_candidate(expected_row, issue), issue) for issue in review),
                         key=lambda x: -x[0])
        matched = [(score, row) for score, row in matched if score > 0][:3]
        out = {
            "model": model, "parameter": parameter, "condition": condition,
            "reference_page": expected_row.get("reference_page", ""),
            "reference_value_for_human_review_only": expected_row.get("reference_value", ""),
            "issue_found": "yes" if matched else "no",
            "candidate_count_in_top3": len(matched),
        }
        for pos in range(1, 4):
            cell = matched[pos-1][1] if pos <= len(matched) else {}
            out[f"candidate_{pos}_reason"] = cell.get("reason", "")
            out[f"candidate_{pos}_page"] = cell.get("source_page", "")
            out[f"candidate_{pos}_row_id"] = cell.get("row_id", "")
            out[f"candidate_{pos}_source_cells"] = " | ".join(map(str, cell.get("cells", [])))
            out[f"candidate_{pos}_ocr_evidence"] = json.dumps(
                cell.get("ocr_evidence", {}), ensure_ascii=False)
        result.append(out)
    return result


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (2, 3):
        print("Usage: diagnose_missing.py expected_photo_160.csv ALL_MODELS_long.csv [NEEDS_REVIEW.json]")
        return 2
    benchmark, long_csv = (Path(p) for p in args[:2])
    review_path = Path(args[2]) if len(args) == 3 else long_csv.with_name("NEEDS_REVIEW.json")
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else []
    if not isinstance(review, list):
        raise ValueError("NEEDS_REVIEW.json must contain a list")
    audit = make_audit(read_csv(benchmark), read_csv(long_csv), review)
    dest = long_csv.with_name("MISSING_REFERENCE_AUDIT.csv")
    columns = ["model", "parameter", "condition", "reference_page",
               "reference_value_for_human_review_only", "issue_found", "candidate_count_in_top3"]
    for i in range(1, 4):
        columns += [f"candidate_{i}_reason", f"candidate_{i}_page",
                    f"candidate_{i}_row_id", f"candidate_{i}_source_cells", f"candidate_{i}_ocr_evidence"]
    with dest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(audit)
    print(f"Missing benchmark entries: {len(audit)}. Wrote {dest}")
    for item in audit:
        print(f"  {item['model']} | {item['parameter']} | {item['condition']} | "
              f"review={item['candidate_1_reason'] or 'NO LINKED REVIEW ROW'}")
    print("Reference values are never inserted into production facts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
