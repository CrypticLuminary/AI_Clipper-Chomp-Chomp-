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

import json
import logging
import tempfile
from pathlib import Path

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
        duration_ms: Total duration in ms.  If None, computed from the WAV.
        config: Diarization configuration.
        audio_stream: Which audio stream to extract (0-based).

    Returns:
        Tuple of (DiarizationResult, turns_json_path, identity_map_path).

    Raises:
        RuntimeError: If the backend is unavailable or FFmpeg fails.
    """
    if not check_backend_available():
        raise RuntimeError(
            f"Diarization backend unavailable: {BACKEND_UNAVAILABLE_REASON}"
        )

    output_dir = output_dir.resolve()
    identity_path = output_dir / "speaker_identity_map.json"
    turns_path = output_dir / "speaker_turns.json"

    # Fail closed before any expensive audio processing if either output file exists
    conflicts: list[Path] = []
    if identity_path.exists():
        conflicts.append(identity_path)
    if turns_path.exists():
        conflicts.append(turns_path)

    if conflicts:
        conflict_names = ", ".join(f"'{p}'" for p in conflicts)
        raise FileExistsError(
            f"Diarization output file already exists at {conflict_names}. "
            "Cannot overwrite existing diarization output. "
            "Please choose a different output directory for new diarization runs."
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    # Convert to WAV
    wav_path = output_dir / "diarization_input.wav"
    try:
        logger.info("Converting %s to WAV for diarization", media_path)
        convert_to_wav(media_path, wav_path, audio_stream=audio_stream)

        # Compute duration from WAV if not provided
        if duration_ms is None:
            import wave
            with wave.open(str(wav_path), "rb") as wf:
                duration_ms = int(wf.getnframes() / wf.getframerate() * 1000)
        if duration_ms <= 0:
            raise ValueError("Media has zero or negative duration")

        # Run diarization
        logger.info("Running diarization (duration: %d ms)", duration_ms)
        result = diarize_audio(wav_path, duration_ms, config)
        logger.info(
            "Diarization complete: %d speakers, %d turns, method=%s",
            result.num_speakers_detected,
            len(result.turns),
            result.method,
        )
    finally:
        # Clean up WAV (it can be large)
        try:
            wav_path.unlink()
        except OSError:
            pass

    # Save identity map template FIRST using exclusive creation to ensure
    # we never overwrite human work even in concurrent execution races.
    speaker_ids = sorted({t.speaker_id for t in result.turns})
    identity_map = build_identity_map(speaker_ids)
    identity_map.save(identity_path)

    # Export speaker turns JSON using exclusive creation ("x" mode) to prevent
    # race conditions or overwriting turns data if a turns file appeared after
    # the preflight check.
    # Note: If this exclusive write fails after identity_map.save() succeeded,
    # the existing turns file is preserved byte-for-byte, though an unconfirmed
    # identity map template was already written to this directory (non-atomic export).
    turns_content = json.dumps(result.to_json_dict(), indent=2, ensure_ascii=False)
    try:
        with open(turns_path, "x", encoding="utf-8") as f:
            f.write(turns_content)
    except FileExistsError as exc:
        raise FileExistsError(
            f"Speaker turns file already exists at '{turns_path}'. "
            "Cannot overwrite existing speaker turns data. "
            "Please choose a different output directory for new diarization runs."
        ) from exc

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
    parts = [
        "chomp",
        f'--input "{media_path}"',
        f'--speaker-turns "{turns_path}"',
        "--acknowledge-permission",
        '--rights-ref "YOUR_RIGHTS_REF"',
    ]
    if main_speaker_id:
        parts.append(f"--main-speaker-id {main_speaker_id}")
    if extra_args:
        parts.extend(extra_args)
    return " \\\n  ".join(parts)
