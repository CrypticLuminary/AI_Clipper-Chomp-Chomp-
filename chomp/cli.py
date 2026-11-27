"""Command line workflow for authorized recordings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .clipwork import approve
from .editorial import OllamaProvider
from .manual import render_manual_preview, rerender
from .pipeline import process
from .reframe import StreamerAnchor


def _render_options(parser: argparse.ArgumentParser, *, optional: bool = False) -> None:
    parser.add_argument("--profile", choices=["preview", "final"], default=None if optional else "preview")
    parser.add_argument("--audio-profile", choices=["original", "balanced"], default=None if optional else "original")
    parser.add_argument("--caption-position", choices=["lower", "middle", "upper"], default=None if optional else "lower")
    parser.add_argument("--layout-plan", type=Path, help="JSON of source-relative layout segments that cover the clip")


def _revision_command(command: str, argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog=f"chomp {command}")
    if command == "rerender":
        p.add_argument("--edit", type=Path, required=True)
        p.add_argument("--output", type=Path, default=Path("outputs"))
        p.add_argument("--start", type=float, help="updated start in source seconds")
        p.add_argument("--end", type=float, help="updated end in source seconds")
        p.add_argument("--captions", type=Path, help="corrected SRT on the updated clip clock")
        p.add_argument("--sound-cues", type=Path, help="replacement sound cues on the source clock")
        p.add_argument("--title", help="set a reviewed title of 3–8 words")
        p.add_argument("--burn-hook", action=argparse.BooleanOptionalAction)
        _render_options(p, optional=True)
        args = p.parse_args(argv)
        action = lambda: rerender(args.edit, args.output, start_s=args.start, end_s=args.end,
                                  profile=args.profile, audio_profile=args.audio_profile,
                                  caption_position=args.caption_position, layout_plan=args.layout_plan,
                                  captions_override=args.captions, title=args.title, burn_hook=args.burn_hook,
                                  sound_cues_path=args.sound_cues)
    else:
        p.add_argument("--review", type=Path, required=True)
        p.add_argument("--clip", type=int, default=1)
        p.add_argument("--reviewer", required=True)
        p.add_argument("--watched", action="store_true", help="confirm the audio, captions and framing after playback")
        p.add_argument("--out", type=Path, required=True)
        args = p.parse_args(argv)
        action = lambda: approve(args.review, args.out, reviewer=args.reviewer, clip_index=args.clip, watched=args.watched)
    try:
        _print(action())
    except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
        _print({"error": str(exc)})
        return 1
    return 0


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from .streambridge.cli import command as source_command, media_options, prepare_input
    from .streambridge.source import BridgeError, looks_remote

    if argv and argv[0] in {"source", "live", "youtube", "twitch"}:
        return source_command(argv[0], argv[1:])
    if argv and argv[0] == "clip":
        from .director.clip import main as clip_main

        return clip_main(argv[1:])
    if argv and argv[0] == "render-director":
        from .director.production import main as production_main

        return production_main(argv[1:])
    if argv and argv[0] in ("rerender", "approve"):
        return _revision_command(argv[0], argv[1:])
    if argv and argv[0] == "director":
        from .director.__main__ import main as director_main

        return director_main(argv[1:])
    if argv and argv[0] == "shots":
        from .shotcomposer.__main__ import main as shots_main

        return shots_main(argv[1:])
    if argv and argv[0] in {"hardware", "evolution-feedback", "evolution-train"}:
        from .evolution_feedback import command
        return command(argv[0], argv[1:])
    p = argparse.ArgumentParser(prog="chomp", description="Find and review highlights from authorized recordings",
                                epilog="Use chomp clip for Director selection. Review commands: chomp rerender, "
                                       "chomp approve, chomp director, and chomp shots")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--input", action="append", required=True, metavar="SOURCE", help="local recording or platform URL with authorized media")
    media_options(p)
    p.add_argument("--output", type=Path, default=Path("outputs"))
    p.add_argument("--engine", choices=["heuristic", "evolution"], default="heuristic")
    p.add_argument("--hardware-profile", choices=["eco", "balanced", "gpu"], default="eco")
    p.add_argument("--evolution-disable", action="append", default=[], choices=[
        "dialogue", "acoustic", "visual", "chat", "exploration", "memory", "hybrid", "snap", "local-review", "quiet-asr"])
    p.add_argument("--evolution-visual-model", type=Path, help="local pinned CLIP vision .onnx file")
    p.add_argument("--evolution-max-semantic-frames", type=int)
    p.add_argument("--evolution-retry", metavar="TAG", help="make a separate review job while reusing analysis caches")
    p.add_argument("--evolution-min-free-mb", type=int, default=128)
    p.add_argument("--evolution-embeddings", type=Path, help="local pinned MiniLM directory")
    p.add_argument("--evolution-audio-model", type=Path, help="local pinned YAMNet .tflite file")
    p.add_argument("--evolution-interest-model", type=Path, help="specific human-trained model version")
    p.add_argument("--evolution-director-model", help="opt in to sending bounded evidence to subscription Codex CLI")
    p.add_argument("--evolution-reasoning-calls", type=int, default=8)
    p.add_argument("--evolution-reasoning-seconds", type=int, default=600)
    p.add_argument("--evolution-min-ms", type=int, default=6000)
    p.add_argument("--evolution-max-ms", type=int, default=45000)
    p.add_argument("--evolution-max-frames", type=int)
    p.add_argument("--evolution-max-audio-windows", type=int)
    p.add_argument("--evolution-memory-mb", type=int, default=128)
    p.add_argument("--rights-ref", required=True, help="creator authorization or campaign permission reference")
    p.add_argument("--acknowledge-permission", action="store_true")
    p.add_argument("--transcript", type=Path, help="JSON segments in source seconds or integer milliseconds")
    p.add_argument("--chat", type=Path, help="optional JSON replay with time, user_id and text")
    p.add_argument("--chat-offset-ms", type=int, default=0)
    p.add_argument("--chat-reaction-lag-ms", type=int, default=0,
                   help="attribute chat bursts to this many milliseconds earlier")
    p.add_argument("--speaker-turns", type=Path, help="optional anonymous speaker turns JSON")
    p.add_argument("--main-speaker-id", help="human-confirmed streamer ID")
    p.add_argument("--transcribe", action="store_true", help="use an installed local Whisper model")
    p.add_argument("--refine-captions", action="store_true", help="get word timing on selected clips with local Whisper")
    p.add_argument("--asr-model", default="small", help="cached model name or local model directory")
    p.add_argument("--asr-device", choices=["cpu", "cuda"], default="cpu")
    p.add_argument("--language", help="optional Whisper language code")
    p.add_argument("--min-score", type=float, default=45)
    p.add_argument("--max-clips", type=int, choices=range(4), default=3)
    p.add_argument("--proposals-per-hour", type=int, default=40)
    p.add_argument("--semantic-model", help="optional installed local Ollama GGUF model")
    p.add_argument("--semantic-port", type=int, default=11434)
    p.add_argument("--semantic-budget", type=int, default=12, help="max candidate judgments per VOD")
    p.add_argument("--facecam-roi", help="source pixel rectangle x,y,w,h")
    p.add_argument("--facecam-model", type=Path, help="optional local YuNet model")
    p.add_argument("--saliency-model", type=Path, help="optional local U2Netp ONNX model for gameplay focus")
    p.add_argument("--person-model", type=Path, help="optional local PP-HumanSeg ONNX model for cutout detection")
    p.add_argument("--streamer-anchor", metavar="MS,X,Y,W,H", help="source time and tight streamer face box for a manual clip")
    p.add_argument("--camera-slot", choices=["top", "bottom", "left", "right"], default="top")
    p.add_argument("--camera-motion", choices=["hold", "pan", "track"], default="pan",
                   help="gameplay crop movement for automatic Shorts framing")
    p.add_argument("--camera-fit", choices=["auto", "contain"], default="auto",
                   help="protect the face with an automatic fill or contain the entire webcam panel")
    p.add_argument("--subject-plan", type=Path, help="source-hashed subject and protected regions for an exact Shorts clip")
    p.add_argument("--title", help="set a truthful title for a manual clip")
    p.add_argument("--burn-hook", action="store_true", help="show the title briefly at the start")
    p.add_argument("--sound-cues", type=Path, help="local audio cue schedule for an exact clip")
    p.add_argument("--gameplay-roi", help="source pixel rectangle x,y,w,h")
    p.add_argument("--layout", choices=["shorts", "auto", "auto-facecam", "facecam-gameplay", "gameplay-only", "fit", "letterbox"],
                   default="shorts", help="shorts tracks the webcam container and action on selected ranges")
    p.add_argument("--audio-stream", type=int, default=0, help="zero-based audio track index")
    p.add_argument("--start", type=float, help="manual start in source seconds")
    p.add_argument("--end", type=float, help="manual end in source seconds")
    p.add_argument("--render", action="store_true", help="render MP4s for review")
    _render_options(p)
    args = p.parse_args(argv)
    if not args.acknowledge_permission:
        p.error("--acknowledge-permission is required")
    if args.facecam_model is not None and args.layout not in {"shorts", "auto-facecam"}:
        p.error("--facecam-model requires a detector layout")
    if (args.saliency_model is not None or args.camera_slot != "top") and args.layout != "shorts":
        p.error("--saliency-model and --camera-slot require --layout shorts")
    if args.person_model is not None and (
        args.layout != "shorts" or args.facecam_roi is not None or args.layout_plan is not None
    ):
        p.error("--person-model requires automatic Shorts framing")
    if (args.camera_motion, args.camera_fit) != ("pan", "auto") and (
        args.layout != "shorts" or args.facecam_roi is not None or args.layout_plan is not None
    ):
        p.error("--camera-motion and --camera-fit apply to automatic Shorts framing")
    if len(args.input) > 1 and (args.chat or args.transcript or args.speaker_turns or args.main_speaker_id or args.layout_plan):
        p.error("Transcript, chat, speaker and layout sidecars apply to one input at a time")

    if len(args.input) > 1 and any((args.media_file, args.media_url, args.media_url_env,
                                    args.twitch_download, args.acquired_job, args.portrait, args.sha256)):
        p.error("Attached media applies to one source at a time")

    def parse_roi(value: str | None, name: str) -> tuple[int, int, int, int] | None:
        if value is None:
            return None
        try:
            x, y, w, h = (int(item.strip()) for item in value.split(","))
        except ValueError:
            p.error(f"{name} needs four comma-separated integers")
        return x, y, w, h

    roi = parse_roi(args.facecam_roi, "--facecam-roi")
    gameplay = parse_roi(args.gameplay_roi, "--gameplay-roi")
    anchor = None
    if args.streamer_anchor is not None:
        if args.start is None or args.layout != "shorts" or args.layout_plan or roi or gameplay:
            p.error("--streamer-anchor requires an exact automatic Shorts clip")
        stamp, _, bounds = args.streamer_anchor.partition(",")
        try:
            at_ms = int(stamp)
        except ValueError:
            p.error("--streamer-anchor starts with a source timestamp in integer milliseconds")
        anchor = StreamerAnchor(at_ms, parse_roi(bounds, "--streamer-anchor face box"))
    shared = {
        "rights_ref": args.rights_ref, "transcript_path": args.transcript, "facecam_roi": roi,
        "gameplay_roi": gameplay, "layout": args.layout, "audio_stream": args.audio_stream,
        "facecam_model": args.facecam_model, "layout_plan": args.layout_plan, "profile": args.profile,
        "saliency_model": args.saliency_model, "camera_slot": args.camera_slot,
        "person_model": args.person_model,
        "camera_motion": args.camera_motion, "camera_fit": args.camera_fit,
        "caption_position": args.caption_position, "audio_profile": args.audio_profile,
        "asr_model": args.asr_model, "asr_device": args.asr_device, "asr_language": args.language,
    }
    if (args.start is None) != (args.end is None):
        p.error("--start and --end must be supplied together")
    if args.subject_plan is not None and (args.start is None or args.layout != "shorts"
                                          or args.layout_plan or roi or gameplay):
        p.error("--subject-plan needs an exact automatic Shorts clip without a manual layout or crop")
    if args.start is not None:
        if len(args.input) != 1 or not args.render:
            p.error("Manual preview needs one --input and --render")
        if args.speaker_turns or args.main_speaker_id or args.chat or args.semantic_model:
            p.error("Manual preview uses the requested interval without ranking or speaker attribution")
        try:
            media, link = prepare_input(args.input[0], args)
            report = render_manual_preview(
                media, args.output, start_s=args.start, end_s=args.end,
                transcribe=args.refine_captions or (args.transcribe and args.transcript is None), **shared,
                title=args.title, burn_hook=args.burn_hook, sound_cues_path=args.sound_cues,
                subject_plan_path=args.subject_plan, streamer_anchor=anchor,
                **({"source_link": link.provenance()} if link else {}),
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            _print({"source": "URL input" if looks_remote(args.input[0]) else args.input[0], "error": str(exc),
                    **({"source_error": exc.public()} if isinstance(exc, BridgeError) else {})})
            return 1
        _print(report)
        return 0

    if args.title is not None:
        p.error("--title applies to an exact manual interval or a rerender")
    if args.sound_cues is not None:
        p.error("--sound-cues applies to an exact manual interval or a rerender")
    evolution = evolution_provider = None
    if args.engine == "evolution":
        from .evolution import EvolutionConfig
        from .director.provider import CodexCLI
        if args.semantic_model:
            p.error("Use --evolution-director-model for EVOLUTION structured reasoning")
        try:
            evolution = EvolutionConfig(
                profile=args.hardware_profile, disabled=tuple(sorted(set(args.evolution_disable))),
                embeddings=args.evolution_embeddings, audio_model=args.evolution_audio_model,
                visual_model=args.evolution_visual_model, max_semantic_frames=args.evolution_max_semantic_frames,
                retry=args.evolution_retry, min_free_mb=args.evolution_min_free_mb,
                min_ms=args.evolution_min_ms, max_ms=args.evolution_max_ms, model=args.evolution_interest_model,
                reasoning_calls=args.evolution_reasoning_calls, reasoning_seconds=args.evolution_reasoning_seconds,
                max_frames=args.evolution_max_frames, max_audio_windows=args.evolution_max_audio_windows,
                memory_mb=args.evolution_memory_mb,
            )
            if args.evolution_director_model:
                evolution_provider = CodexCLI(args.evolution_director_model)
        except ValueError as exc:
            p.error(str(exc))
    try:
        provider = OllamaProvider(args.semantic_model, port=args.semantic_port) if args.semantic_model else None
    except ValueError as exc:
        p.error(str(exc))
    failures = 0
    for src in args.input:
        safe_source = "URL input" if looks_remote(src) else src
        try:
            media, link = prepare_input(src, args)
            if link:
                safe_source = link.source.canonical_url or (src if link.source.platform == "local" else link.source.source_id)
            result = process(
                media, args.output, chat_path=args.chat, chat_offset_ms=args.chat_offset_ms,
                reaction_lag_ms=args.chat_reaction_lag_ms, speaker_turns_path=args.speaker_turns,
                main_speaker_id=args.main_speaker_id, transcribe=args.transcribe,
                refine_captions=args.refine_captions, min_score=args.min_score, limit=args.max_clips, render=args.render,
                proposals_per_hour=args.proposals_per_hour, editorial_provider=provider,
                semantic_budget=args.semantic_budget, burn_hook=args.burn_hook, evolution=evolution,
                evolution_provider=evolution_provider, **shared,
                **({"source_link": link.provenance()} if link else {}),
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            _print({"source": safe_source, "error": str(exc),
                    **({"source_error": exc.public()} if isinstance(exc, BridgeError) else {})})
            failures += 1
            continue
        if result.get("engine") == "evolution" and result.get("analysis_status") == "partial":
            failures += 1
        _print({"job_id": result["job_id"], "source": safe_source, "candidates": result["candidate_count"],
                "review_clips": result["selected_for_review"], "review_page": result["review_page"],
                "candidate_ledger": result["candidate_ledger"], "reused_job": result.get("reused_job", False),
                "review_queue": str(args.output.resolve() / result["job_id"] / "review_queue.json"),
                **({"source_link": result["source_link"]} if "source_link" in result else {}),
                **({"evidence": result["evolution_evidence"], "analysis_status": result["analysis_status"]}
                   if result.get("engine") == "evolution" else {})})
    return int(failures != 0)
