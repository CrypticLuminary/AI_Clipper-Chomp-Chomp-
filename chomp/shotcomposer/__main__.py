"""chomp shots: provider-neutral propose / validate / render boundary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..artifacts import artifact_key, write_json
from ..director.contract import read_json, schema
from ..director.evidence import read_verified_bundle
from .adapters import attach_tracking, from_editorial, legacy_envelope
from .capabilities import manifest
from .compiler import compile_plan
from .contract import validate
from .workflow import prepare, render, revise, verify_source


def _canvas(parser):
    parser.add_argument("--width", type=int, default=540)
    parser.add_argument("--height", type=int, default=960)
    parser.add_argument("--fps", default="30", help="explicit output rational, e.g. 30000/1001")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chomp shots", description="Compose and render Director plans for review")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities", help="machine-readable operators, bounds, availability and ownership")
    variants = commands.add_parser("variants", help="assess 1–3 Director alternatives without selecting or substituting one")
    variants.add_argument("--source", type=Path, required=True)
    variants.add_argument("--evidence", type=Path, required=True)
    variants.add_argument("--plan", type=Path, action="append", required=True)
    schemas = commands.add_parser("schema")
    schemas.add_argument("--kind", choices=["plan", "evidence"], default="plan")
    setup = commands.add_parser("prepare", help="create a provider-neutral Director pack; no semantic analysis or model call")
    setup.add_argument("--source", type=Path, required=True)
    setup.add_argument("--out", type=Path, required=True)
    setup.add_argument("--start-ms", type=int, required=True)
    setup.add_argument("--end-ms", type=int, required=True)
    setup.add_argument("--rights-ref", required=True)
    setup.add_argument("--acknowledge-permission", action="store_true", required=True)
    setup.add_argument("--audio-stream", type=int, default=0)
    setup.add_argument("--evidence", type=Path)
    setup.add_argument("--bundle", dest="bundle_path", type=Path, help="verified EDIT-01 bundle to include with its frames")
    setup.add_argument("--editorial", dest="editorial_path", type=Path, help="selected Director storyboard from that bundle")
    _canvas(setup)
    for action in ("validate", "compile", "render"):
        p = commands.add_parser(action)
        p.add_argument("--source", type=Path, required=True)
        p.add_argument("--plan", type=Path, required=True)
        p.add_argument("--evidence", type=Path, required=True)
        if action == "render":
            p.add_argument("--out", type=Path, required=True, help="new review directory; existing revisions are never replaced")
            p.add_argument("--experimental", action="store_true", help=argparse.SUPPRESS)
            p.add_argument("--captions", type=Path)
            p.add_argument("--caption-position", choices=["lower", "middle", "upper"], default="lower")
    lower = commands.add_parser("from-director", help="lower an existing editorial plan using reviewed controller region bindings")
    for name in ("source", "editorial", "bundle", "evidence", "bindings", "out"):
        lower.add_argument(f"--{name}", type=Path, required=True)
    _canvas(lower)
    track = commands.add_parser("attach-tracking", help="attach source-matched FX02 observations without inventing interval visibility")
    for name in ("source", "evidence", "edit", "out"):
        track.add_argument(f"--{name}", type=Path, required=True)
    migrate = commands.add_parser("migrate", help="freeze a lossless v0.5 compatibility envelope, not an approximate new render")
    migrate.add_argument("--edit", type=Path, required=True)
    migrate.add_argument("--out", type=Path, required=True)
    revision = commands.add_parser("revise", help="save a typed replacement revision linked to its immutable parent")
    for name in ("previous", "replacement", "evidence", "out"):
        revision.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        action = args.command
        if action == "capabilities":
            result = manifest()
        elif action == "variants":
            if not 1 <= len(args.plan) <= 3:
                raise ValueError("Supply 1–3 alternatives")
            evidence = read_json(args.evidence)
            verify_source(args.source, evidence)
            candidates = [read_json(path) for path in args.plan]
            ranges = {(p["start_ms"], p["end_ms"]) for p in candidates}
            if len(ranges) != 1:
                raise ValueError("Alternatives must use the same selected source range")
            result = {"status": "alternatives_assessed", "selected": None,
                      "alternatives": [validate(p, evidence).as_dict() for p in candidates],
                      "note": "Director or reviewer chooses explicitly; no automatic fallback"}
        elif action == "schema":
            result = schema("shot_plan" if args.kind == "plan" else "shot_evidence")
        elif action == "prepare":
            options = vars(args).copy()
            for key in ("command", "acknowledge_permission"):
                del options[key]
            options["evidence"] = read_json(args.evidence) if args.evidence else None
            result = prepare(**options)
        elif action in {"validate", "compile", "render"}:
            plan, evidence = read_json(args.plan), read_json(args.evidence)
            if action == "render":
                result = render(args.source, args.out, plan, evidence, captions=args.captions,
                                caption_position=args.caption_position)
            else:
                verify_source(args.source, evidence)
                result = validate(plan, evidence).as_dict()
                if action == "compile" and result["status"] == "renderable":
                    compiled = compile_plan(plan, evidence)
                    result = {**compiled.report, "video_filtergraph": compiled.graph}
        elif action == "from-director":
            evidence = read_json(args.evidence)
            verify_source(args.source, evidence)
            bundle, _ = read_verified_bundle(args.bundle, args.source)
            bindings = read_json(args.bindings)
            editorial = read_json(args.editorial)
            plan = from_editorial(editorial, bundle, evidence, bindings, width=args.width, height=args.height, fps=args.fps)
            result = validate(plan, evidence).as_dict()
            args.out.mkdir(parents=True, exist_ok=False)
            for name, value in (("plan", plan), ("evidence", evidence), ("bindings", bindings), ("editorial", editorial), ("validation", result)):
                write_json(args.out / f"{name}.json", value, exclusive=True)
            result["directory"] = str(args.out.resolve())
        elif action == "attach-tracking":
            evidence = read_json(args.evidence)
            verify_source(args.source, evidence)
            revised, summary = attach_tracking(evidence, read_json(args.edit))
            args.out.mkdir(parents=True, exist_ok=False)
            write_json(args.out / "evidence.json", revised, exclusive=True)
            write_json(args.out / "tracking-context.json", summary, exclusive=True)
            result = {"status": "tracking_attached", "evidence_sha256": artifact_key(revised),
                      "directory": str(args.out.resolve()), "note": "Update plan evidence hash; samples do not prove interval visibility"}
        elif action == "migrate":
            result = legacy_envelope(read_json(args.edit))
            write_json(args.out, result, exclusive=True)
        else:
            plan = revise(read_json(args.previous), read_json(args.replacement))
            result = validate(plan, read_json(args.evidence)).as_dict()
            write_json(args.out, plan, exclusive=True)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 2 if result.get("status") in {"blocked", "needs_review"} else 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ZeroDivisionError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
