"""Deterministic source-clock boundary refinement; no unverified story claims."""
from __future__ import annotations

from dataclasses import dataclass

from .models import Candidate, TranscriptSegment, checked_span
from .signals import nearby_text


@dataclass(frozen=True)
class BoundaryDecision:
    start_ms: int
    end_ms: int
    method: str
    note: str


def refine_boundaries(
    candidate: Candidate,
    transcript: list[TranscriptSegment],
    duration_ms: int,
    *,
    max_length_ms: int = 60_000,
    min_length_ms: int = 8_000,
    context_ms: int = 6_000,
) -> BoundaryDecision:
    """Expand to nearby utterance boundaries; cap long clips around their anchor.

    This is *not* semantic setup/payoff recognition. The result is provisional
    until the user reviews the source footage.
    """
    candidate.validate(duration_ms)
    if not 0 < min_length_ms <= max_length_ms:
        raise ValueError("invalid clip length configuration")
    if context_ms < 0:
        raise ValueError("context_ms must be nonnegative")
    start, end = candidate.start_ms, candidate.end_ms
    nearby = [s for s in transcript if s.end_ms >= start - context_ms and s.start_ms <= end + context_ms]
    method = "signal_padding"
    if nearby:
        start = max(0, min(start, min(s.start_ms for s in nearby) - 500))
        end = min(duration_ms, max(end, max(s.end_ms for s in nearby) + 1000))
        method = "transcript_assisted"
    # Do not let a long, continuous discussion become an unbounded clip.
    if end - start > max_length_ms:
        start = max(start, min(candidate.anchor_ms - round(max_length_ms * .45), end - max_length_ms))
        end = start + max_length_ms
        method += "_capped"
    if end - start < min_length_ms and duration_ms >= min_length_ms:
        missing = min_length_ms - (end - start)
        start = max(0, start - missing // 2)
        end = min(duration_ms, start + min_length_ms)
        start = max(0, end - min_length_ms)
    checked_span(start, end, duration_ms)
    candidate.start_ms, candidate.end_ms = start, end
    candidate.transcript_excerpt = nearby_text(transcript, start, end)
    candidate.validate(duration_ms)
    return BoundaryDecision(start, end, method, "Provisional utterance/padding boundaries; human review required")
