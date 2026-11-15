"""CLI entry point for standalone speaker diarization.

Usage::

    python -m chomp.diarization --media input.mp4 --output outputs/diarize [options]

This outputs:
    1. speaker_turns.json (consumable via chomp --speaker-turns)
    2. speaker_identity_map.json (template for human streamer confirmation)

Once reviewed, the pipeline can be invoked with:
    chomp --input input.mp4 --speaker-turns outputs/diarize/speaker_turns.json \\
          --main-speaker-id SPEAKER_00 --rights-ref ... --acknowledge-permission
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .core import (
    DiarizationConfig,
    check_backend_available,
    BACKEND_UNAVAILABLE_REASON,
)
from .integration import diarize_and_export, generate_pipeline_command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m chomp.diarization",
        description="Extract anonymous speaker turns from gaming/livestream VODs",
    )
    parser.add_argument(
        "--media",
        required=True,
        type=Path,
        help="Input media file (video or audio)",
    )
    parser.add_argument(
        "--output",
        default=Path("outputs/diarization"),
        type=Path,
        help="Output directory for diarization artifacts",
    )
    parser.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Expected number of speakers (omit for automatic detection, 2-6)",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "sherpa", "resemblyzer"],
        default="auto",
        help="Diarization backend (default: auto)",
    )
    parser.add_argument(
        "--audio-stream",
        type=int,
        default=0,
        help="Audio stream index to extract (0-based)",
    )
    parser.add_argument(
        "--min-segment-ms",
        type=int,
        default=500,
        help="Minimum speech segment length in ms",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.25,
        help="Confidence threshold for speaker clustering",
    )
    parser.add_argument(
        "--segmentation-model",
        type=Path,
        default=None,
        help="Path to sherpa-onnx segmentation ONNX model file",
    )
    parser.add_argument(
        "--embedding-model",
        type=Path,
        default=None,
        help="Path to sherpa-onnx speaker embedding ONNX model file",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    config = DiarizationConfig(
        num_speakers=args.num_speakers,
        min_segment_ms=args.min_segment_ms,
        confidence_threshold=args.confidence_threshold,
        backend=args.backend,
        segmentation_model=args.segmentation_model,
        embedding_model=args.embedding_model,
    )

    if not check_backend_available(config):
        if args.backend == "sherpa":
            print(
                "ERROR: sherpa-onnx backend unavailable or model files not configured.\n"
                "Ensure sherpa-onnx is installed and existing model files are specified via "
                "--segmentation-model and --embedding-model.",
                file=sys.stderr,
            )
        elif args.backend == "resemblyzer":
            print(
                f"ERROR: resemblyzer backend unavailable.\n{BACKEND_UNAVAILABLE_REASON}",
                file=sys.stderr,
            )
        else:
            print(
                f"ERROR: No usable diarization backend available.\n{BACKEND_UNAVAILABLE_REASON}\n"
                "If using sherpa-onnx, ensure existing model files are provided via "
                "--segmentation-model and --embedding-model.",
                file=sys.stderr,
            )
        return 1

    if not args.media.exists():
        print(f"ERROR: Media file not found: {args.media}", file=sys.stderr)
        return 1

    try:
        result, turns_path, id_path = diarize_and_export(
            args.media,
            args.output,
            config=config,
            audio_stream=args.audio_stream,
        )
    except Exception as exc:
        print(f"ERROR: Diarization failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({
        "status": "success",
        "num_speakers_detected": result.num_speakers_detected,
        "turns_count": len(result.turns),
        "method": result.method,
        "speaker_turns": str(turns_path),
        "identity_map": str(id_path),
        "warnings": result.warnings,
        "suggested_pipeline_command": generate_pipeline_command(
            args.media, turns_path
        ),
    }, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
