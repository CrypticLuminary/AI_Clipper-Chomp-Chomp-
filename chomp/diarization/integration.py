"""Integration between diarization subsystem and the existing pipeline.

This module bridges diarization output to the pipeline's ``--speaker-turns``
JSON contract and transcript attribution system.

Usage pattern (outside the pipeline, as proposed integration):

    1. User runs diarization on their VOD audio.
    2. Diarization produces a speaker-turns JSON file.
    3. User reviews the output and optionally edits the identity map.
    4. User passes ``--speaker-turns`` and ``--main-speaker-id`` to the pipeline.

This keeps the pipeline (``chomp/pipeline.py``) unmodified while providing
a full diarization workflow.
"""
from __future__ import annotations

import os
import shlex
import logging
import tempfile
from pathlib import Path

from ..artifacts import artifact_lock, promote_new, source_identity, write_json
from ..media import check_audio_track, probe
from ..models import SpeakerTurn, TranscriptSegment
from ..speakers import attribute_segments, read_speaker_turns
from .core import (
    DiarizationConfig,
    DiarizationResult,
    check_backend_available,
    convert_to_wav,
    diarize_audio,
    BACKEND_UNAVAILABLE_REASON,
)
from .identity import SpeakerIdentityMap, build_identity_map

logger = logging.getLogger(__name__)


def diarize_and_export(
    media_path: Path,
    output_dir: Path,
    *,
    duration_ms: int | None = None,
    config: DiarizationConfig | None = None,
    audio_stream: int = 0,
) -> tuple[DiarizationResult, Path, Path]:
    """Run diarization on a media file and export results.

    This is the main entry point for standalone diarization.  It:
    1. Converts the media to 16 kHz mono WAV (via FFmpeg).
    2. Runs the diarization engine.
    3. Exports speaker turns as JSON (compatible with ``--speaker-turns``).
    4. Exports an identity map template.

    Parameters:
        media_path: Path to the source media file.
        output_dir: Where to write output files.
        duration_ms: Source duration in ms. If None, measured by FFprobe.
        config: Diarization configuration.
        audio_stream: Which audio stream to extract (0-based).

    Returns:
        Tuple of (DiarizationResult, turns_json_path, identity_map_path).

    Raises:
        RuntimeError: If the backend is unavailable or FFmpeg fails.
    """
    if not check_backend_available(config):
        raise RuntimeError(f"Diarization backend unavailable: {BACKEND_UNAVAILABLE_REASON}")

    media_path = media_path.resolve()
    output_dir = output_dir.resolve()
    identity_path = output_dir / "speaker_identity_map.json"
    turns_path = output_dir / "speaker_turns.json"

    def check_conflicts() -> None:
        conflicts = [path for path in (identity_path, turns_path) if path.exists()]
        if conflicts:
            names = ", ".join(f"'{path}'" for path in conflicts)
            raise FileExistsError(
                f"Diarization output already exists at {names}. Choose a different output directory."
            )

    with artifact_lock(output_dir.parent / f".{output_dir.name}.diarization.lock", timeout=5):
        check_conflicts()
        if duration_ms is None:
            meta = probe(media_path)
            check_audio_track(meta, audio_stream)
            duration_ms = meta["duration_ms"]
        if type(duration_ms) is not int or duration_ms <= 0:
            raise ValueError("Media duration must be a positive integer in milliseconds")
        if type(audio_stream) is not int or audio_stream < 0:
            raise ValueError("Audio stream must be a nonnegative integer")
        recording = source_identity(media_path)

        with tempfile.TemporaryDirectory(prefix=".diarize-", dir=output_dir.parent,
                                         ignore_cleanup_errors=True) as temp:
            scratch = Path(temp)
            wav = scratch / "input.wav"
            logger.info("Converting %s to WAV for diarization", media_path)
            convert_to_wav(media_path, wav, audio_stream=audio_stream)
            result = diarize_audio(wav, duration_ms, config)
            wav.unlink(missing_ok=True)

            speaker_ids = sorted({turn.speaker_id for turn in result.turns} - {"SPEAKER_UNKNOWN"})
            identity = build_identity_map(speaker_ids)
            identity_staged, turns_staged = scratch / identity_path.name, scratch / turns_path.name
            identity.save(identity_staged)
            write_json(turns_staged, {**result.to_json_dict(), "source_clock": {
                "source_id": recording, "duration_ms": duration_ms, "audio_stream": audio_stream,
            }})
            check_conflicts()
            if not output_dir.exists():
                scratch.rename(output_dir)
            else:
                # Existing directories may contain unrelated work. Claim both
                # names exclusively and undo our own claim if the second fails.
                original = identity_staged.stat()
                promote_new(identity_staged, identity_path)
                try:
                    promote_new(turns_staged, turns_path)
                except BaseException:
                    current = identity_path.stat() if identity_path.exists() else None
                    if current and (current.st_ino, current.st_size, current.st_mtime_ns) == (
                        original.st_ino, original.st_size, original.st_mtime_ns
                    ):
                        identity_path.unlink()
                    raise
    return result, turns_path, identity_path


def merge_diarization_with_transcript(
    turns: list[SpeakerTurn],
    segments: list[TranscriptSegment],
) -> list[TranscriptSegment]:
    """Attribute transcript segments using diarization turns.

    This delegates to the existing ``attribute_segments`` function from
    ``chomp.speakers``, which already implements conservative attribution
    (only labels a segment when one speaker clearly dominates).

    Parameters:
        turns: Speaker turns from diarization.
        segments: Transcript segments (from ASR or manual).

    Returns:
        List of TranscriptSegments with speaker_id attributed where confident.
    """
    return attribute_segments(segments, turns)


def generate_pipeline_command(
    media_path: Path,
    turns_path: Path,
    main_speaker_id: str | None = None,
    *,
    extra_args: list[str] | None = None,
) -> str:
    """Generate the CLI command to run the pipeline with diarization output.

    This is a convenience for the user — shows them exactly how to integrate
    the diarization results with the existing pipeline.
    """
    def quote(value: object) -> str:
        text = str(value)
        if os.name == "nt":
            return "'" + text.replace("'", "''") + "'"
        return shlex.quote(text)

    parts = ["chomp", "--input", quote(media_path), "--speaker-turns", quote(turns_path),
             "--acknowledge-permission", "--rights-ref", quote("YOUR_RIGHTS_REF")]
    if main_speaker_id:
        parts.extend(["--main-speaker-id", quote(main_speaker_id)])
    if extra_args:
        parts.extend(quote(arg) for arg in extra_args)
    return " ".join(parts)
