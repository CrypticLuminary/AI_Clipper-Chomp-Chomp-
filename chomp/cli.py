"""CLI for local authorized completed recordings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import process
from .manual import render_manual_preview


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
    p.add_argument("--facecam-roi", help="source pixel rectangle x,y,w,h for confirmed streamer webcam")
    p.add_argument("--facecam-model", type=Path, help="optional local YuNet ONNX file for --layout auto-facecam")
    p.add_argument("--gameplay-roi", help="optional source pixel rectangle x,y,w,h for gameplay area")
    p.add_argument(
        "--layout", choices=["auto", "auto-facecam", "facecam-gameplay", "gameplay-only", "fit", "letterbox"],
        default="auto", help="auto-facecam detects stable facecams, with a full-frame fit fallback",
    )
    p.add_argument("--audio-stream", type=int, default=0, help="0-based audio track index (defaults to first audio stream)")
    p.add_argument("--start", type=float, help="manual preview start in source seconds; requires --end and --render")
    p.add_argument("--end", type=float, help="manual preview end in source seconds; requires --start and --render")
    p.add_argument("--render", action="store_true", help="create captioned 9:16 review MP4s (NOT auto-posted)")
    args = p.parse_args(argv)
    if not args.acknowledge_permission:
        p.error("--acknowledge-permission required; a URL/file alone does not grant reuse rights")
    if args.facecam_model is not None and args.layout != "auto-facecam":
        p.error("--facecam-model requires --layout auto-facecam")
    if len(args.input)>1 and (args.chat or args.transcript or args.speaker_turns or args.main_speaker_id):
        p.error("--chat / --transcript / speaker options currently apply to one input at a time")
    def parse_roi(value: str | None, name: str) -> tuple[int, int, int, int] | None:
        if not value:
            return None
        try:
            coords = tuple(int(x.strip()) for x in value.split(","))
            if len(coords) != 4:
                raise ValueError("expected 4 values")
            return coords
        except ValueError:
            p.error(f"{name} must be four comma-separated integers: x,y,w,h")

    roi = parse_roi(args.facecam_roi, "--facecam-roi")
    gameplay = parse_roi(args.gameplay_roi, "--gameplay-roi")
    if (args.start is None) != (args.end is None):
        p.error("--start and --end must be supplied together")
    if args.start is not None:
        if len(args.input) != 1 or not args.render:
            p.error("manual --start/--end requires one --input and --render")
        if args.transcribe or args.speaker_turns or args.main_speaker_id or args.chat:
            p.error("manual preview accepts optional --transcript but not ASR/speaker/chat processing")
        try:
            report = render_manual_preview(
                Path(args.input[0]), Path(args.output), rights_ref=args.rights_ref,
                start_s=args.start, end_s=args.end,
                transcript_path=Path(args.transcript) if args.transcript else None,
                facecam_roi=roi, gameplay_roi=gameplay, layout=args.layout,
                audio_stream=args.audio_stream, facecam_model=args.facecam_model,
            )
        except Exception as exc:
            print(json.dumps({"source":args.input[0], "error":str(exc)}))
            return 1
        print(json.dumps(report, ensure_ascii=False))
        return 0
    success = 0
    for src in args.input:
        try:
            result = process(
                Path(src), Path(args.output), rights_ref=args.rights_ref,
                transcript_path=Path(args.transcript) if args.transcript else None,
                chat_path=Path(args.chat) if args.chat else None, chat_offset_ms=args.chat_offset_ms,
                speaker_turns_path=Path(args.speaker_turns) if args.speaker_turns else None,
                main_speaker_id=args.main_speaker_id, transcribe=args.transcribe, asr_model=args.asr_model,
                asr_device=args.asr_device, min_score=args.min_score, limit=args.max_clips, render=args.render,
                facecam_roi=roi, gameplay_roi=gameplay, layout=args.layout, audio_stream=args.audio_stream,
                facecam_model=args.facecam_model,
            )
        except Exception as exc:
            print(json.dumps({"source":src,"error":str(exc)}))
            continue
        print(json.dumps({"job_id":result["job_id"],"source":src,"candidates":result["candidate_count"],"review_clips":result["selected_for_review"],"review_queue":str(Path(args.output).resolve()/result["job_id"]/"review_queue.json")},ensure_ascii=False))
        success+=1
    return 0 if success==len(args.input) else 1
