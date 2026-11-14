"""High-recall, deliberately interpretable audio/transcript/chat evidence lanes."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import math
import statistics

from .models import ChatMessage, Observation, TranscriptSegment


def _secs_to_ms(value: object) -> int:
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("nonfinite timestamp")
    return round(f * 1000)


def read_transcript(path: Path | None, duration_ms: int) -> list[TranscriptSegment]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    segments = payload.get("segments", []) if isinstance(payload, dict) else payload
    out = []
    for item in segments:
        if "start_ms" in item:
            start, end = int(item["start_ms"]), int(item["end_ms"])
        else:
            start, end = _secs_to_ms(item["start"]), _secs_to_ms(item["end"])
        seg = TranscriptSegment(start, end, str(item["text"]))
        seg.validate(duration_ms)
        out.append(seg)
    return sorted(out, key=lambda s: (s.start_ms, s.end_ms))


def read_chat(path: Path | None, duration_ms: int, offset_ms: int = 0) -> list[ChatMessage]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    messages = payload.get("messages", []) if isinstance(payload, dict) else payload
    out = []
    for item in messages:
        time = int(item["time_ms"]) if "time_ms" in item else _secs_to_ms(item["time"])
        msg = ChatMessage(time + offset_ms, str(item.get("user_id", "unknown")), str(item["text"]))
        msg.validate(duration_ms)
        out.append(msg)
    return sorted(out, key=lambda m: m.time_ms)


def audio_observations(curve: list[float], duration_ms: int) -> list[Observation]:
    """Detect per-VOD loudness departures, not automatic amusement/viral scores."""
    if not curve:
        return []
    global_baseline = statistics.median(curve)
    out: list[Observation] = []
    for i, level in enumerate(curve):
        local = statistics.median(curve[max(0, i-12):min(len(curve), i+13)])
        delta = level - max(local, global_baseline - 8)
        if delta < 5:
            continue
        start = i * 1000
        end = min(duration_ms, start + 1000)
        if end > start:
            out.append(Observation("audio", "relative_loudness_peak", start, end, round(min(1.0, delta/20.0), 4), f"{level:.1f} dBFS, +{delta:.1f} dB above local baseline"))
    return out


def transcript_observations(segments: list[TranscriptSegment], duration_ms: int) -> list[Observation]:
    """Broad text proposals. No assumption that uppercase/exclamation implies virality."""
    if not segments:
        return []
    out: list[Observation] = []
    # One proposal for each active 30s speech bucket, including quiet narration.
    buckets: dict[int, list[TranscriptSegment]] = {}
    for s in segments:
        buckets.setdefault(s.start_ms // 30000, []).append(s)
    for b, items in buckets.items():
        start = max(0, min(s.start_ms for s in items))
        end = min(duration_ms, max(s.end_ms for s in items))
        if end <= start:
            continue
        words = sum(len(s.text.split()) for s in items)
        surprise_cues = sum(s.text.count("?") + s.text.count("!") for s in items)
        # Never fully exclude calm/quiet speech; even modest speech is a discovery proposal.
        strength = min(.95, .18 + min(.50, words/160) + min(.20, surprise_cues*.08))
        out.append(Observation("transcript", "speech_window", start, end, round(strength, 4), f"{words} words; punctuation cues {surprise_cues}"))
    return out


def chat_observations(messages: list[ChatMessage], duration_ms: int) -> list[Observation]:
    if not messages:
        return []
    bins: dict[int, list[ChatMessage]] = {}
    for msg in messages:
        bins.setdefault(msg.time_ms // 10000, []).append(msg)
    rates = [len(bins.get(i, [])) for i in range((duration_ms + 9999) // 10000)]
    baseline = statistics.median(rates) if rates else 0
    out: list[Observation] = []
    for b, msgs in bins.items():
        users = len({m.user_id for m in msgs})
        # At least 3 distinct users guards against two-person chat spam.
        if users < 3 or len(msgs) < max(4, baseline*1.5):
            continue
        start, end = b*10000, min(duration_ms, (b+1)*10000)
        if end > start:
            out.append(Observation("chat", "audience_swell", start, end, round(min(1., users/12), 4), f"{len(msgs)} messages; {users} distinct users"))
    return out


def nearby_text(segments: list[TranscriptSegment], start_ms: int, end_ms: int, max_chars: int = 2200) -> str:
    return " ".join(s.text.strip() for s in segments if s.end_ms > start_ms and s.start_ms < end_ms)[:max_chars]
