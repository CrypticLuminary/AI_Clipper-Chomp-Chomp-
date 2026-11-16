"""Broad source-clock audio, transcript, and optional chat observations."""
from __future__ import annotations

from pathlib import Path
import json
import math
import statistics

from .models import ChatMessage, Observation, TimedWord, TranscriptSegment


def _secs_to_ms(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("timestamps cannot be booleans")
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("nonfinite timestamp")
    return round(f * 1000)


def _rows(path: Path, field: str) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = payload.get(field) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
        raise ValueError(f"{path.name} requires a list of {field} objects")
    return rows


def read_transcript(path: Path | None, duration_ms: int) -> list[TranscriptSegment]:
    if path is None:
        return []
    out = []
    for item in _rows(path, "segments"):
        if "start_ms" in item:
            start, end = item["start_ms"], item["end_ms"]
        else:
            start, end = _secs_to_ms(item["start"]), _secs_to_ms(item["end"])
        words = item.get("words", [])
        if not isinstance(words, list) or any(not isinstance(word, dict) for word in words):
            raise ValueError("Transcript words must be a list of timestamped objects")
        seg = TranscriptSegment(start, end, item["text"], item.get("speaker_id", item.get("speaker")),
                                tuple(TimedWord(**word) for word in words))
        seg.validate(duration_ms)
        out.append(seg)
    return sorted(out, key=lambda s: (s.start_ms, s.end_ms))


def read_chat(path: Path | None, duration_ms: int, offset_ms: int = 0) -> list[ChatMessage]:
    if path is None:
        return []
    if type(offset_ms) is not int:
        raise ValueError("Chat offset must be integer milliseconds")
    out = []
    for item in _rows(path, "messages"):
        time = item["time_ms"] if "time_ms" in item else _secs_to_ms(item["time"])
        if type(time) is not int:
            raise ValueError("Chat time_ms must be integer milliseconds")
        msg = ChatMessage(time + offset_ms, item.get("user_id", "unknown"), item["text"])
        msg.validate(duration_ms)
        out.append(msg)
    return sorted(out, key=lambda m: m.time_ms)


def audio_observations(curve: list[float], duration_ms: int, *, hop_ms: int = 1000) -> list[Observation]:
    """Propose transient changes and peaks; their source may be game audio or speech."""
    if hop_ms <= 0:
        raise ValueError("Audio hop must be positive")
    if not curve:
        return []
    global_baseline = statistics.median(curve)
    radius = max(1, 12_000 // hop_ms)
    lookback = max(1, 1500 // hop_ms)
    out: list[Observation] = []
    current: list[Observation] = []
    for i, level in enumerate(curve):
        local = statistics.median(curve[max(0, i-radius):i+radius+1])
        recent = statistics.median(curve[max(0, i-lookback):i]) if i else level
        floor = max(local, global_baseline - 12, -68)
        departure = level - floor
        onset = min(level - recent, max(0, level - (global_baseline - 5)))
        strength_db = max(departure, onset * .72)
        start, end = i * hop_ms, min(duration_ms, (i + 1) * hop_ms)
        if strength_db >= 5 and end > start:
            current.append(Observation("audio", "transient" if onset * .72 > departure else "relative_loudness_peak",
                                       start, end, round(min(1.0, strength_db/20.0), 4),
                                       f"{level:.1f} dBFS, local departure {departure:+.1f} dB"))
        elif current:
            out.append(max(current, key=lambda observation: observation.strength))
            current = []
    if current:
        out.append(max(current, key=lambda observation: observation.strength))
    return out


def _speech_observation(items: list[TranscriptSegment], start_ms: int, end_ms: int) -> Observation:
    words = sum(len(s.text.split()) for s in items)
    # Capture speech presence without pretending punctuation measures payoff.
    density = min(.40, words / 95)
    strength = .24 + density
    return Observation("transcript", "speech_window", start_ms, end_ms, round(strength, 4),
                       f"{words} timed words or words in complete segments")


def transcript_observations(segments: list[TranscriptSegment], duration_ms: int) -> list[Observation]:
    """Give even quiet speech a proposal. Long ASR segments get multiple anchors."""
    buckets: dict[int, list[TranscriptSegment]] = {}
    hop = 18_000
    for seg in segments:
        if not seg.text.strip():
            continue
        for bucket in range(seg.start_ms // hop, (seg.end_ms - 1) // hop + 1):
            buckets.setdefault(bucket, []).append(seg)
    out = []
    for bucket, items in sorted(buckets.items()):
        start = max(bucket * hop, min(s.start_ms for s in items))
        end = min(duration_ms, (bucket + 1) * hop, max(s.end_ms for s in items))
        if end > start:
            out.append(_speech_observation(items, start, end))
    return out


def chat_observations(messages: list[ChatMessage], duration_ms: int, *, reaction_lag_ms: int = 0) -> list[Observation]:
    if type(reaction_lag_ms) is not int or not 0 <= reaction_lag_ms <= 30_000:
        raise ValueError("Chat reaction lag must be between 0 and 30000 ms")
    if not messages:
        return []
    hop = 5000
    bins: dict[int, list[tuple[int, ChatMessage]]] = {}
    for msg in messages:
        at = max(0, msg.time_ms - reaction_lag_ms)
        if at < duration_ms:
            bins.setdefault(at // hop, []).append((at, msg))
    baseline = statistics.median(len(bins.get(i, [])) for i in range((duration_ms + hop - 1) // hop))
    out = []
    for bucket, msgs in sorted(bins.items()):
        users = len({m.user_id for _, m in msgs if m.user_id and m.user_id != "unknown"})
        if users < 3 or len(msgs) < max(4, baseline * 1.5):
            continue
        # Keep the actual aligned event span and describe the configured lag.
        start, end = min(at for at, _ in msgs), min(duration_ms, max(at for at, _ in msgs) + 1)
        out.append(Observation("chat", "audience_swell", start, end, round(min(1., users/12), 4),
                               f"{len(msgs)} messages; {users} distinct users; {reaction_lag_ms} ms reaction lag"))
    return out


def nearby_text(segments: list[TranscriptSegment], start_ms: int, end_ms: int, max_chars: int = 2200) -> str:
    return " ".join(s.text.strip() for s in segments if s.end_ms > start_ms and s.start_ms < end_ms)[:max_chars]


def contained_speech(segments: list[TranscriptSegment], start_ms: int, end_ms: int) -> list[TranscriptSegment]:
    """Only claim words supported inside an exported clip."""
    out = []
    for seg in segments:
        if seg.start_ms >= end_ms or seg.end_ms <= start_ms:
            continue
        if start_ms <= seg.start_ms and seg.end_ms <= end_ms:
            if seg.text.strip():
                out.append(seg)
        elif seg.words:
            words = tuple(w for w in seg.words if start_ms <= w.start_ms and w.end_ms <= end_ms)
            if words:
                out.append(TranscriptSegment(words[0].start_ms, max(w.end_ms for w in words),
                                             " ".join(w.text for w in words), seg.speaker_id, words))
    return out


def final_evidence(observations: list[Observation], segments: list[TranscriptSegment],
                   start_ms: int, end_ms: int) -> tuple[list[Observation], list[TranscriptSegment]]:
    speech = contained_speech(segments, start_ms, end_ms)
    evidence = [o for o in observations if o.modality in {"audio", "chat"}
                and start_ms <= o.start_ms and o.end_ms <= end_ms]
    if speech:
        evidence.append(_speech_observation(speech, min(s.start_ms for s in speech), max(s.end_ms for s in speech)))
    return sorted(evidence, key=lambda o: (o.start_ms, o.end_ms, o.modality)), speech
