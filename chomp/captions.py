"""Sidecar SRT captions using existing timestamped transcript segments.

Segment-level timing is approximate unless the caller supplies word-aligned segments.
"""
from __future__ import annotations

import re
from .models import TranscriptSegment, checked_span


def _clock(milliseconds: int) -> str:
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def _chunks(text: str, maximum: int = 72) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    # Stop user-supplied markup being interpreted as subtitle styling.
    clean = clean.replace("<", "‹").replace(">", "›").replace("{", "（").replace("}", "）")
    if not clean:
        return []
    output: list[str] = []
    current = ""
    for word in clean.split(" "):
        if current and len(current) + len(word) + 1 > maximum:
            output.append(current)
            current = ""
        if len(word) > maximum:
            if current:
                output.append(current)
                current = ""
            output.extend(word[i:i+maximum] for i in range(0,len(word),maximum))
        else:
            current = f"{current} {word}".strip()
    if current:
        output.append(current)
    return output


def make_srt(segments: list[TranscriptSegment], start_ms: int, end_ms: int, duration_ms: int) -> tuple[str, int]:
    """Make clip-local SRT cues; split long source segments by approximate text length."""
    checked_span(start_ms, end_ms, duration_ms)
    cues: list[tuple[int,int,str]] = []
    for seg in sorted(segments, key=lambda x:(x.start_ms,x.end_ms)):
        seg.validate(duration_ms)
        left = max(start_ms, seg.start_ms)
        right = min(end_ms, seg.end_ms)
        if right - left < 80:
            continue
        texts = _chunks(seg.text)
        if not texts:
            continue
        available = right - left
        # Approximate subdivisions when only sentence timestamps exist.
        weights = [len(t) for t in texts]
        total = sum(weights)
        cursor = left
        for idx, value in enumerate(texts):
            next_time = right if idx == len(texts)-1 else left + round(available * sum(weights[:idx+1]) / total)
            if next_time - cursor >= 80:
                cues.append((cursor-start_ms,next_time-start_ms,value))
            cursor = next_time
    lines: list[str] = []
    for index, (left,right,value) in enumerate(cues,1):
        lines.extend([str(index), f"{_clock(left)} --> {_clock(right)}", value, ""])
    return "\n".join(lines) + ("\n" if lines else ""), len(cues)
