"""Standalone ClipLedger entry point: python -m chomp.evaluation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .benchmark import benchmark, create_event_template, markdown_report, validate_dataset, validate_events
from ..artifacts import write_json as artifact_json, write_text as artifact_text
from .core import (
    ClipLedgerError, compare, evaluate, make_template, read_annotations,
    read_manifest, write_json,
)


def _paths(parser: argparse.ArgumentParser, *, prefix: str = "") -> None:
    parser.add_argument(
        f"--{prefix}manifest", required=True,
        help="Path to pipeline review_queue.json",
    )
    parser.add_argument(
        f"--{prefix}reviews", required=True,
        help="Path to independent ClipLedger annotation JSON",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m chomp.evaluation",
        description="Human clip reviews and source-event evaluation.",
    )
    cmds = parser.add_subparsers(dest="command", required=True)
    make = cmds.add_parser("template", help="Create once; never overwrite existing labels")
    make.add_argument("manifest", help="Pipeline review_queue.json")
    make.add_argument("--out", required=True, help="New human-editable review JSON")
    make.add_argument(
        "--source-id", help="Explicit ID for one unchanged source recording "
        "(required across runs if the source paths differ)",
    )

    validate = cmds.add_parser("validate", help="Validate review identities and labels")
    _paths(validate)
    validate.add_argument(
        "--require-complete", action="store_true",
        help="Require every selected clip to have a decision and all rubric fields",
    )

    report = cmds.add_parser("report", help="Generate denominator-aware JSON metrics")
    _paths(report)
    report.add_argument("--out", help="Output JSON report; otherwise print to stdout")

    compare_cmd = cmds.add_parser("compare", help="Compare reviewed runs on same source clock")
    _paths(compare_cmd, prefix="old-")
    _paths(compare_cmd, prefix="new-")
    compare_cmd.add_argument("--out", help="Output JSON comparison; otherwise stdout")

    events = cmds.add_parser("events-template", help="Create a blank source-event annotation file")
    events.add_argument("--candidates", type=Path, required=True)
    events.add_argument("--reviewer", required=True)
    events.add_argument("--out", type=Path, required=True)
    bench = cmds.add_parser("benchmark", help="Compare proposals with independently annotated source events")
    bench.add_argument("--candidates", type=Path, required=True)
    bench.add_argument("--events", type=Path, required=True)
    bench.add_argument("--manifest", type=Path)
    bench.add_argument("--reviews", type=Path, action="append", default=[])
    bench.add_argument("--technical", type=Path, help="source/run-bound decoded technical measurements")
    bench.add_argument("--out", type=Path, help="new JSON report; never overwrites inputs or existing files")
    bench.add_argument("--markdown", type=Path, help="new readable report")
    for name in ("events-validate", "dataset-validate"):
        command = cmds.add_parser(name)
        command.add_argument("path", type=Path)
    experiments = cmds.add_parser("compare-experiments", help="Matched A/B/C/D run comparison")
    experiments.add_argument("--config", type=Path, required=True)
    experiments.add_argument("--out", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            manifest = read_manifest(args.manifest)
            created = make_template(manifest, args.out, source_id=args.source_id)
            print(json.dumps({
                "created": created, "path": args.out,
                "source_job_id": manifest.job_id,
                "selected_count": len(manifest.clips),
                "message": "Template created" if created else
                           "Existing validated review preserved without changes",
            }, indent=2))
        elif args.command == "validate":
            manifest = read_manifest(args.manifest)
            annotation = read_annotations(
                manifest, args.reviews, require_complete=args.require_complete,
            )
            summary = evaluate(manifest, annotation)
            print(json.dumps({
                "valid": True, "require_complete": args.require_complete,
                "selected_count": summary["selected_count"],
                "review_coverage": summary["review_coverage"],
                "previews": summary["previews"],
            }, indent=2))
        elif args.command == "report":
            manifest = read_manifest(args.manifest)
            reviews = read_annotations(manifest, args.reviews)
            result = evaluate(manifest, reviews)
            output = write_json(
                result, args.out, protected=(args.manifest, args.reviews),
            )
            if args.out is None:
                sys.stdout.write(output)
            else:
                print(f"ClipLedger report written to {args.out}")
        elif args.command == "compare":
            old = read_manifest(args.old_manifest)
            new = read_manifest(args.new_manifest)
            older = read_annotations(old, args.old_reviews)
            newer = read_annotations(new, args.new_reviews)
            result = compare(old, older, new, newer)
            output = write_json(
                result, args.out,
                protected=(
                    args.old_manifest, args.old_reviews,
                    args.new_manifest, args.new_reviews,
                ),
            )
            if args.out is None:
                sys.stdout.write(output)
            else:
                print(f"ClipLedger comparison written to {args.out}")
        elif args.command == "events-template":
            create_event_template(args.candidates, args.out, reviewer=args.reviewer)
            print(f"Source-event template written to {args.out}")
        elif args.command == "benchmark":
            result = benchmark(args.events, args.candidates, manifest_path=args.manifest,
                               reviews=tuple(args.reviews), technical_path=args.technical)
            if args.out:
                artifact_json(args.out, result, exclusive=True)
            if args.markdown:
                artifact_text(args.markdown, markdown_report(result), exclusive=True)
            if not args.out:
                print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        elif args.command in {"events-validate", "dataset-validate"}:
            data = json.loads(args.path.read_text(encoding="utf-8-sig"))
            result = validate_events(data) if args.command == "events-validate" else validate_dataset(data)
            print(json.dumps({"valid": True, "schema_version": data["schema_version"]}))
        elif args.command == "compare-experiments":
            from .experiments import compare_experiments
            artifact_json(args.out, compare_experiments(args.config), exclusive=True)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
