"""Generate a fully synthetic ClipLedger example (never real human judgments).

Run: python -m chomp.evaluation.demo --out example-directory
The directory must not exist: example generation never overwrites reviews.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import (
    compare, evaluate, make_template, read_annotations, read_manifest, write_json,
)


def generate(folder: Path) -> dict:
    """Create two fake v0.4 review_queue files, human-like DEMO labels and reports."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)

    def run(name: str, spans: list[tuple[int, int, int]], decisions: list[str]) -> tuple:
        directory = folder / name
        directory.mkdir()
        path = directory / "review_queue.json"
        fake_manifest = {
            "schema_version": "0.4",
            "job_id": f"SYNTHETIC-{name}",
            "source": "synthetic://clipledger-example-source",
            "duration_ms": 120_000,
            "candidate_count": len(spans),
            "selected_for_review": len(spans),
            "clips": [
                {
                    "candidate": {"start_ms": start, "end_ms": end, "anchor_ms": anchor,
                                  "score": 99.0},  # never used for labels or metrics
                    "preview": None,
                    "edit_spec": None,
                    "review_status": "pending",
                }
                for start, end, anchor in spans
            ],
        }
        path.write_text(json.dumps(fake_manifest, indent=2) + "\n", encoding="utf-8")
        manifest = read_manifest(path)
        review_path = directory / "synthetic_reviews.json"
        make_template(manifest, review_path, source_id="synthetic://clipledger-example-source")
        reviews = read_annotations(manifest, review_path)
        for row, decision in zip(reviews["clips"], decisions):
            row.update({
                "reviewer_id": "SYNTHETIC_TEST_FIXTURE",
                "decision": decision,
                "hook_quality": 4 if decision == "good" else 2,
                "context_completeness": 3,
                "payoff_completeness": 4 if decision == "good" else 2,
                "audio_correct": "n/a", "visual_correct": "n/a",
                "subtitle_readable": "n/a", "duplicate_content": "no",
                "failure_category": "weak_hook" if decision == "bad" else None,
                "notes": "SYNTHETIC LABEL ONLY; no person watched a clip.",
            })
        # The demo is explicitly the only place fabricating review judgments.
        # Real-world evaluations use a blank template and a human editor.
        review_path.write_text(
            json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        validated = read_annotations(manifest, review_path, require_complete=True)
        write_json(evaluate(manifest, validated), directory / "report.json")
        return manifest, validated

    old, older = run("old", [
        (1_000, 9_000, 5_000),
        (18_000, 26_000, 22_000),
        (40_000, 50_000, 45_000),
    ], ["good", "bad", "uncertain"])
    new, newer = run("new", [
        (2_000, 10_000, 5_500),
        (19_000, 27_000, 22_500),
        (75_000, 82_000, 78_000),
    ], ["good", "good", "bad"])
    comparison = compare(old, older, new, newer)
    write_json(comparison, folder / "comparison.json")
    return {
        "folder": str(folder),
        "old_review": str(folder / "old" / "synthetic_reviews.json"),
        "new_review": str(folder / "new" / "synthetic_reviews.json"),
        "old_report": str(folder / "old" / "report.json"),
        "new_report": str(folder / "new" / "report.json"),
        "comparison": str(folder / "comparison.json"),
        "warning": "Synthetic labels are illustrative, NOT human evaluation results.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create disposable, entirely synthetic ClipLedger example data")
    parser.add_argument("--out", type=Path, required=True, help="New, nonexistent folder")
    args = parser.parse_args()
    try:
        result = generate(args.out)
    except FileExistsError:
        parser.error("Demo output folder exists; refusing to overwrite any annotations")
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
