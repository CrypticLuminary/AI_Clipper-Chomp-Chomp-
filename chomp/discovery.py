"""Source-clock safe observation clustering and candidate proposals."""
from __future__ import annotations

from .models import Candidate, Observation, TranscriptSegment
from .signals import nearby_text


def discover(observations: list[Observation], words: list[TranscriptSegment], duration_ms: int, *, before_ms: int = 9000, after_ms: int = 14000, max_proposals: int = 70) -> list[Candidate]:
    if duration_ms <= 0:
        raise ValueError("duration must be positive")
    expanded: list[Candidate] = []
    for obs in observations:
        obs.validate(duration_ms)
        anchor = (obs.start_ms + obs.end_ms)//2
        start = max(0, anchor - before_ms)
        end = min(duration_ms, anchor + after_ms)
        if end <= start:
            continue
        expanded.append(Candidate(start, end, anchor, [obs]))
    expanded.sort(key=lambda c: (c.start_ms, c.end_ms))
    merged: list[Candidate] = []
    for c in expanded:
        if merged and c.start_ms <= merged[-1].end_ms and c.anchor_ms - merged[-1].anchor_ms <= 22000:
            p = merged[-1]
            p.end_ms = max(p.end_ms, c.end_ms)
            p.start_ms = min(p.start_ms, c.start_ms)
            p.observations.extend(c.observations)
            strongest = max(p.observations, key=lambda o:o.strength)
            p.anchor_ms = (strongest.start_ms + strongest.end_ms)//2
        else:
            merged.append(c)
    # Candidate proposal budget preserves a mix of signals; do not claim recall guarantee.
    merged.sort(key=lambda c: max(o.strength for o in c.observations), reverse=True)
    out = merged[:max_proposals]
    for c in out:
        c.transcript_excerpt = nearby_text(words, max(0,c.start_ms-15000), min(duration_ms,c.end_ms+15000))
        c.validate(duration_ms)
    return out
