"""Local job operations shared with the Studio HTTP service."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil

from ..clipcontrol import ClipControl
from .jobs import Manager, read, safe_path
from .server import StudioHandler, serve


def command(group, argv):
    if group == "serve":
        parser = argparse.ArgumentParser(prog="chomp serve")
        parser.add_argument("--port", type=int, default=8765)
        parser.add_argument("--output", type=Path, default=Path("outputs/studio"))
        args = parser.parse_args(argv)
        if not 0 <= args.port <= 65535:
            parser.error("Choose a valid local port")
        serve(args.port, args.output)
        return 0
    parser = argparse.ArgumentParser(prog="chomp job")
    parser.add_argument("--store", type=Path, default=Path("outputs/studio"))
    sub = parser.add_subparsers(dest="action", required=True)
    start = sub.add_parser("create", aliases=["start"])
    start.add_argument("--source")
    start.add_argument("--file", type=Path)
    start.add_argument("--transcript", type=Path)
    start.add_argument("--requirements", type=Path)
    start.add_argument("--rights-ref", required=True)
    start.add_argument("--acknowledge-permission", action="store_true")
    start.add_argument("--media-url-env")
    start.add_argument("--feed-url-env")
    start.add_argument("--live", action="store_true")
    start.add_argument("--spool", type=Path)
    start.add_argument("--live-seconds", type=int, default=300)
    start.add_argument("--twitch-download", action="store_true")
    start.add_argument("--provider-model")
    start.add_argument("--confirm-provider", action="store_true")
    start.add_argument("--asr-model", default="small")
    start.add_argument("--asr-device", choices=("cpu", "cuda"), default="cpu")
    start.add_argument("--transcribe", action=argparse.BooleanOptionalAction, default=True)
    start.add_argument("--timeout", type=int, default=3600)
    check = sub.add_parser("validate")
    check.add_argument("requirements", type=Path)
    sub.add_parser("list")
    for action in ("status", "cancel", "retry", "clips", "download", "zip", "revise", "feedback"):
        p = sub.add_parser(action)
        p.add_argument("job_id")
        if action in {"download", "revise", "feedback"}:
            p.add_argument("clip", type=int)
        if action in {"download", "zip"}:
            p.add_argument("--out", type=Path, required=True)
        if action == "revise":
            p.add_argument("--start-ms", type=int, required=True)
            p.add_argument("--end-ms", type=int, required=True)
            p.add_argument("--layout")
            for which in ("facecam-roi", "gameplay-roi"):
                p.add_argument("--" + which, type=int, nargs=4, metavar=("X", "Y", "W", "H"))
        if action == "feedback":
            p.add_argument("--decision", required=True, choices=("approve", "reject", "uncertain"))
            p.add_argument("--note", default="")
    args = parser.parse_args(argv)
    manager = Manager(args.store)
    store = manager.store
    job = None
    try:
        if args.action in {"create", "start"}:
            def url(name):
                if name and not os.getenv(name):
                    raise ValueError("The named URL environment variable is empty")
                return os.getenv(name) if name else None
            config = {"source": args.source, "local_file": str(args.file.resolve()) if args.file else None,
                      "local_transcript": str(args.transcript.resolve()) if args.transcript else None,
                      "local_spool": str(args.spool.resolve()) if args.spool else None,
                      "requirements": read(args.requirements) if args.requirements else {}, "rights_ref": args.rights_ref,
                      "confirmed": args.acknowledge_permission, "media_url": url(args.media_url_env),
                      "feed_url": url(args.feed_url_env), "live_seconds": args.live_seconds,
                      "kind": "live" if args.live or args.spool else "recording", "transcribe": args.transcribe,
                      "twitch_download": args.twitch_download, "provider_model": args.provider_model,
                      "provider_confirmed": args.confirm_provider, "asr_model": args.asr_model,
                      "asr_device": args.asr_device, "timeout": args.timeout}
            job = manager.start(config, trusted_local=True)
        elif args.action == "retry":
            job = manager.retry(args.job_id)
        elif args.action == "revise":
            job = manager.revise(args.job_id, args.clip, start_ms=args.start_ms, end_ms=args.end_ms,
                                 layout=args.layout, facecam_roi=args.facecam_roi, gameplay_roi=args.gameplay_roi)
        elif args.action == "validate":
            response = ClipControl.parse(read(args.requirements)).public()
        elif args.action == "list":
            response = {"jobs": [store.public(row) for row in store.list()]}
        elif args.action == "status":
            response = store.public(store.status(args.job_id))
        elif args.action == "cancel":
            response = manager.cancel(args.job_id)
        elif args.action == "clips":
            response = store.clips(args.job_id)
        elif args.action == "feedback":
            response = store.feedback(args.job_id, args.clip, args.decision, args.note)
        else:
            if args.action == "zip":
                path = StudioHandler.zip(args.job_id, store)
            else:
                path = safe_path(store.root, store.clip(args.job_id, args.clip)["preview"])
            with args.out.open("xb") as destination, path.open("rb") as source:
                shutil.copyfileobj(source, destination)
            response = {"path": str(args.out.resolve())}
        if job:
            import sys
            print(json.dumps(job, ensure_ascii=True), flush=True, file=sys.stderr)
            response = manager.wait(job["id"])
        print(json.dumps(response, ensure_ascii=True), flush=True)
        return int(response.get("status") in {"failed", "cancelled"})
    except KeyboardInterrupt:
        if job:
            manager.cancel(job["id"])
            manager.wait(job["id"])
        return 130
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)[:500]}, ensure_ascii=True))
        return 1