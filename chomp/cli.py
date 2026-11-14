"""CLI for local authorized completed recordings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import process


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="chomp", description="Chomp-Chomp VOD highlight baseline (no AI provider yet)")
    p.add_argument("--input", action="append", required=True, metavar="LOCAL_VIDEO", help="local VOD file; repeat for batch")
    p.add_argument("--output", default="outputs")
    p.add_argument("--rights-ref", required=True, help="your creator authorization/campaign permission reference")
    p.add_argument("--acknowledge-permission", action="store_true", help="confirm the material is authorized for the intended workflow")
    p.add_argument("--transcript", help="optional JSON segments with start/end in seconds or start_ms/end_ms")
    p.add_argument("--chat", help="optional JSON replay with time/user_id/text")
    p.add_argument("--chat-offset-ms", type=int, default=0)
    p.add_argument("--speaker-turns", help="optional external diarization JSON: anonymous speaker turns")
    p.add_argument("--main-speaker-id", help="confirmed streamer ID from labelled transcript/diarization; never inferred")
    p.add_argument("--transcribe", action="store_true", help="run local faster-whisper if --transcript absent")
    p.add_argument("--asr-model", default="small")
    p.add_argument("--asr-device", choices=["cpu", "cuda"], default="cpu")
    p.add_argument("--min-score", type=float, default=45)
    p.add_argument("--max-clips", type=int, default=3)
    p.add_argument("--facecam-roi", help="optional x,y,w,h in original video pixels (fixed split layout)")
    p.add_argument("--render", action="store_true", help="create captioned 9:16 review MP4s (NOT auto-posted)")
    args = p.parse_args(argv)
    if not args.acknowledge_permission:
        p.error("--acknowledge-permission required; a URL/file alone does not grant reuse rights")
    if len(args.input)>1 and (args.chat or args.transcript or args.speaker_turns or args.main_speaker_id):
        p.error("--chat / --transcript / speaker options currently apply to one input at a time")
    roi = None
    if args.facecam_roi:
        try:
            parts = tuple(int(x.strip()) for x in args.facecam_roi.split(","))
            if len(parts) != 4:
                raise ValueError("four integers required")
            roi = parts
        except ValueError:
            p.error("--facecam-roi must be 4 comma-separated integers: x,y,w,h")
    success = 0
    for src in args.input:
        try:
            result = process(Path(src),Path(args.output),rights_ref=args.rights_ref, transcript_path=Path(args.transcript) if args.transcript else None,chat_path=Path(args.chat) if args.chat else None,chat_offset_ms=args.chat_offset_ms,speaker_turns_path=Path(args.speaker_turns) if args.speaker_turns else None,main_speaker_id=args.main_speaker_id,transcribe=args.transcribe,asr_model=args.asr_model,asr_device=args.asr_device,min_score=args.min_score,limit=args.max_clips,render=args.render,facecam_roi=roi)
        except Exception as exc:
            print(json.dumps({"source":src,"error":str(exc)}))
            continue
        print(json.dumps({"job_id":result["job_id"],"source":src,"candidates":result["candidate_count"],"review_clips":result["selected_for_review"],"review_queue":str(Path(args.output).resolve()/result["job_id"]/"review_queue.json")},ensure_ascii=False))
        success+=1
    return 0 if success==len(args.input) else 1
