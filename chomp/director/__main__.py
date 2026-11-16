"""CLI for evidence preparation, experimental judgment and validation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .contract import read_json, validate_plan
from .evidence import build_bundle, read_verified_bundle
from .experiment import compare
from .provider import CodexCLI


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "shots":
        from ..shotcomposer.__main__ import main as shots_main

        return shots_main(argv[1:])
    parser = argparse.ArgumentParser(prog="chomp director", description="Evaluate bounded editorial plans on local evidence")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="freeze source-clock evidence and a few decoded stills")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True, help="new immutable bundle directory")
    prepare.add_argument("--case-id", required=True)
    prepare.add_argument("--split", choices=["development", "holdout"], required=True)
    prepare.add_argument("--rights-ref", required=True)
    prepare.add_argument("--acknowledge-permission", action="store_true", required=True)
    prepare.add_argument("--candidate-id", required=True)
    prepare.add_argument("--start-ms", type=int, required=True)
    prepare.add_argument("--end-ms", type=int, required=True)
    prepare.add_argument("--anchor-ms", type=int, required=True)
    prepare.add_argument("--context-before-ms", type=int, default=10_000)
    prepare.add_argument("--context-after-ms", type=int, default=10_000)
    prepare.add_argument("--audio-stream", type=int, default=0)
    prepare.add_argument("--transcript", type=Path)
    prepare.add_argument("--chat", type=Path)
    prepare.add_argument("--chat-offset-ms", type=int, default=0)
    prepare.add_argument("--frame-ms", type=int, action="append", default=[])
    prepare.add_argument("--regions", type=Path, help="optional annotations keyed by requested frame milliseconds")

    pair = commands.add_parser("compare", help="run two read-only Codex requests with an existing subscription")
    pair.add_argument("--source", type=Path, required=True)
    pair.add_argument("--bundle", type=Path, required=True)
    pair.add_argument("--out", type=Path, required=True)
    pair.add_argument("--model", required=True, help="pinned model for both modes")
    pair.add_argument("--timeout", type=int, default=150)

    verify = commands.add_parser("validate", help="independently verify source evidence and optionally a saved plan")
    verify.add_argument("--source", type=Path, required=True)
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--plan", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            options = vars(args).copy()
            del options["command"], options["acknowledge_permission"]
            source, out = options.pop("source"), options.pop("out")
            options["frame_times"] = tuple(options.pop("frame_ms"))
            path = build_bundle(source, out, **options)
            result = {"status": "frozen", "bundle": str(path), "sha256": read_json(path)["evidence_hash"]}
        elif args.command == "compare":
            result = compare(args.bundle, args.source, args.out, CodexCLI(args.model, args.timeout))
        else:
            bundle, images = read_verified_bundle(args.bundle, args.source)
            if args.plan is not None:
                validate_plan(read_json(args.plan), bundle)
            result = {"status": "validated", "bundle_sha256": bundle["evidence_hash"],
                      "verified_frames": len(images), "plan_checked": args.plan is not None}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    if args.command == "compare":
        return int(len(result["results"]) != 2 or any(row["status"] != "validated" for row in result["results"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
