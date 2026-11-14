"""CLI for local authorized completed recordings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import process


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="chomp", description="Chomp-Chomp local-first VOD highlight baseline")
    p.add_argument("--input", action="append", required=True, metavar="LOCAL_VIDEO", help="local VOD file; repeat for batch")
    p.add_argument("--output", default="outputs")
    p.add_argument("--rights-ref", required=True, help="your creator authorization/campaign permission reference")
    p.add_argument("--acknowledge-permission", action="store_true", help="confirm the material is authorized for the intended workflow")
    p.add_argument("--transcript", help="optional JSON segments with start/end in seconds or start_ms/end_ms")
    p.add_argument("--chat", help="optional JSON replay with time/user_id/text")
    p.add_argument("--chat-offset-ms", type=int, default=0)
    p.add_argument("--transcribe", action="store_true", help="run local faster-whisper if --transcript absent")
    p.add_argument("--asr-model", default="small")
    p.add_argument("--asr-device", choices=["cpu", "cuda"], default="cpu")
    p.add_argument("--ollama-model", help="optional local Ollama model used for semantic quality ranking")
    p.add_argument("--min-score", type=float, default=45)
    p.add_argument("--max-clips", type=int, default=3)
    p.add_argument("--render", action="store_true", help="create letterboxed preview MP4s (NOT auto-posted)")
    args = p.parse_args(argv)
    if not args.acknowledge_permission:
        p.error("--acknowledge-permission required; a URL/file alone does not grant reuse rights")
    if len(args.input)>1 and (args.chat or args.transcript):
        p.error("--chat / --transcript currently apply to one input at a time")
    success = 0
    for src in args.input:
        try:
            result = process(Path(src),Path(args.output),rights_ref=args.rights_ref, transcript_path=Path(args.transcript) if args.transcript else None,chat_path=Path(args.chat) if args.chat else None,chat_offset_ms=args.chat_offset_ms,transcribe=args.transcribe,asr_model=args.asr_model,asr_device=args.asr_device,ollama_model=args.ollama_model,min_score=args.min_score,limit=args.max_clips,render=args.render)
        except Exception as exc:
            print(json.dumps({"source":src,"error":str(exc)}))
            continue
        print(json.dumps({"job_id":result["job_id"],"source":src,"candidates":result["candidate_count"],"review_clips":result["selected_for_review"],"review_queue":str(Path(args.output).resolve()/result["job_id"]/"review_queue.json")},ensure_ascii=False))
        success+=1
    return 0 if success==len(args.input) else 1
