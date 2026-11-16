"""Speaker-labelled transcript support without claiming automatic voice identification.

A diarizer can output anonymous speaker IDs; identifying the *main streamer*
requires a trusted source track, human confirmation or a separate voice-matching
step. A speech recognizer alone does not do this.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import json
from pathlib import Path

from .models import SpeakerTurn, TranscriptSegment
from .signals import _secs_to_ms


def read_speaker_turns(path: Path | None, duration_ms: int, *, source_id: str | None = None,
                       audio_stream: int | None = None) -> list[SpeakerTurn]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    provenance = payload.get("source_clock") if isinstance(payload, dict) else None
    if provenance is not None:
        if not isinstance(provenance, dict) or provenance.get("duration_ms") != duration_ms:
            raise ValueError("Speaker turns were generated for a different source duration")
        if source_id is not None and provenance.get("source_id") != source_id:
            raise ValueError("Speaker turns belong to a different recording")
        if audio_stream is not None and provenance.get("audio_stream") != audio_stream:
            raise ValueError("Speaker turns were generated from a different audio track")
    rows = payload.get("turns", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("Speaker turn file must be a list or {turns: [...]} object")
    out = []
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("Every speaker turn must be an object")
        if "start_ms" in item:
            start, end = item["start_ms"], item["end_ms"]
        else:
            start, end = _secs_to_ms(item["start"]), _secs_to_ms(item["end"])
        speaker = item.get("speaker_id", item.get("speaker"))
        turn = SpeakerTurn(start, end, speaker)
        turn.validate(duration_ms)
        out.append(turn)
    return sorted(out, key=lambda x: (x.start_ms, x.end_ms, x.speaker_id))


def attribute_segments(segments: list[TranscriptSegment], turns: list[SpeakerTurn]) -> list[TranscriptSegment]:
    """Assign anonymous IDs only if one diarized speaker clearly dominates.

    Reject mixed/overlapping speaker speech rather than confidently labelling a
    segment which spans two voices. Existing transcript labels take precedence.
    """
    if not turns:
        return segments
    result = []
    for s in segments:
        if s.speaker_id is not None:
            result.append(s)
            continue
        intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for t in turns:
            a, b = max(s.start_ms, t.start_ms), min(s.end_ms, t.end_ms)
            if a < b:
                intervals[t.speaker_id].append((a, b))
        coverage: dict[str, int] = {}
        for speaker, spans in intervals.items():
            covered = 0
            last_end = s.start_ms
            for a, b in sorted(spans):
                covered += max(0, b - max(a, last_end))
                last_end = max(b, last_end)
            coverage[speaker] = covered
        ranked = sorted(coverage.items(), key=lambda x: x[1], reverse=True)
        span = s.end_ms-s.start_ms
        if (not ranked or ranked[0][0] == "SPEAKER_UNKNOWN" or ranked[0][1]/span < .65
                or (len(ranked)>1 and (ranked[0][1]-ranked[1][1])/span < .45)):
            result.append(s)
        else:
            result.append(replace(s, speaker_id=ranked[0][0]))
    return result


def speaker_inventory(segments: list[TranscriptSegment], turns: list[SpeakerTurn], main_speaker_id: str | None) -> list[str]:
    speaker_ids = sorted({x.speaker_id for x in segments if x.speaker_id is not None} | {t.speaker_id for t in turns})
    if main_speaker_id is not None and main_speaker_id not in speaker_ids:
        raise ValueError(f"--main-speaker-id {main_speaker_id!r} does not occur in labelled transcript/speaker turns. Plain Whisper ASR does not identify speakers; supply --speaker-turns or a labelled transcript.")
    return speaker_ids


def candidate_speaker_context(segments: list[TranscriptSegment], start_ms: int, end_ms: int, main_speaker_id: str | None) -> list[dict]:
    """Auditable speech timeline for reviewer and future agent/LLM interpretation."""
    out = []
    for s in segments:
        start, end = max(start_ms,s.start_ms), min(end_ms,s.end_ms)
        if end <= start or not s.text.strip():
            continue
        role = "main_streamer" if main_speaker_id is not None and s.speaker_id == main_speaker_id else "other_speaker" if s.speaker_id and main_speaker_id is not None else "unverified"
        out.append({"start_ms":start,"end_ms":end,"speaker_id":s.speaker_id,"role":role,"text":s.text.strip()})
    return out
