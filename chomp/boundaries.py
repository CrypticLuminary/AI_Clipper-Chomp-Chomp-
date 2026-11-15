"""Conservative, source-clock clip boundary refinement (timing, not semantics).

The discovery window is a *hint*, not a promise that every nearby utterance
belongs in the clip.  This module never ranks clips or moves their event anchor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import math

from .models import Candidate, TranscriptSegment, checked_span
from .signals import nearby_text


@dataclass(frozen=True)
class BoundaryDecision:
    start_ms: int
    end_ms: int
    method: str
    note: str


# Limits are deliberately smaller than the public hard maximum (60 seconds).
# Segment boundaries are *estimates*, not guaranteed word or sentence alignment.
_DEFAULT_SOFT_CAP_MS = 34_000
_NO_TRANSCRIPT_CAP_MS = 30_000
_PAUSE_JOIN_MS = 1_500
_EDGE_BRIDGE_MS = 6_000
_SEED_DISTANCE_MS = 3_000


def _distance_to_span(point: int, segment: TranscriptSegment) -> int:
    return max(segment.start_ms - point, point - segment.end_ms, 0)


def _pad_to_minimum(start: int, end: int, anchor: int, duration: int, minimum: int) -> tuple[int, int]:
    """Grow, never shift an already chosen event out of the interval."""
    desired = min(minimum, duration)
    if end - start >= desired:
        return start, end
    missing = desired - (end - start)
    before = missing // 2
    start = max(0, start - before)
    end = min(duration, end + missing - before)
    if end - start < desired:
        start = max(0, end - desired)
        end = min(duration, start + desired)
    # The caller's interval already contains anchor; padding cannot remove it.
    assert start <= anchor <= end
    return start, end


def _cap_around_anchor(start: int, end: int, anchor: int, duration: int, cap: int) -> tuple[int, int]:
    """Deterministic last resort if one ASR segment exceeds the clip budget."""
    if end - start <= cap:
        return start, end
    # Prefer a little more aftermath than setup, without treating a spike as payoff.
    lo = max(start, anchor - (cap * 45 // 100))
    lo = min(lo, end - cap)
    lo = min(lo, duration - cap)
    hi = lo + cap
    return lo, hi


def _silence_snap(start: int, end: int, anchor: int, levels: Sequence[float], hop: int,
                  duration: int) -> tuple[int, int, bool]:
    """Optional *absolute quiet* hints only; NEVER peak/event detection.

    Each level is a one-bin dBFS reading beginning at i * hop. The bins are
    supplied by the caller, typically from an already-computed scan_audio
    curve. The scan isn't repeated here. Only use bins within 1.5 seconds of
    the provisional edges, and do not cross the anchor.
    """
    if not levels:
        return start, end, False
    reach = 1_500
    begin_idx = max(0, (start - reach) // hop)
    finish_idx = min(len(levels), (end + reach) // hop + 2)
    quiet = [i for i in range(begin_idx, finish_idx)
             if math.isfinite(levels[i]) and levels[i] <= -50]
    beginnings = [i * hop for i in quiet if 0 <= i * hop <= anchor and abs(i * hop - start) <= reach]
    endings = [min(duration, (i + 1) * hop) for i in quiet
               if anchor <= min(duration, (i + 1) * hop) and abs(min(duration, (i + 1) * hop) - end) <= reach]
    changed = False
    if beginnings:
        new_start = min(beginnings, key=lambda t: (abs(t - start), t))
        changed |= new_start != start
        start = new_start
    if endings:
        new_end = min(endings, key=lambda t: (abs(t - end), t))
        changed |= new_end != end
        end = new_end
    return start, end, changed


def refine_boundaries(
    candidate: Candidate,
    transcript: list[TranscriptSegment],
    duration_ms: int,
    *,
    max_length_ms: int = 60_000,
    min_length_ms: int = 8_000,
    context_ms: int = 6_000,
    audio_levels: Sequence[float] | None = None,
    audio_hop_ms: int = 1_000,
) -> BoundaryDecision:
    """Refine an approximate event timestamp to a bounded, reviewable clip.

    * The anchor never moves. The discovery span does not force full inclusion.
    * Anchor-adjacent transcript segments form a seed; nearby turns are joined
      across <=1.5s pauses, with 8s/10s typical side budgets (12s/16s hard).
      One nearby longer gap (up to 6s) per side may bridge a setup/payoff
      pause within a hard 12s/16s side budget.
    * Very long ASR segments cannot be split safely by segment timing; a hard
      cut is then necessary and explicitly labelled as such.
    * If no anchor-near transcript exists, a deterministic candidate-centred
      timing fallback is used, optionally snapping to quiet audio bins.

    Transcript, speaker and audio signals here indicate timing only. Neither
    speech punctuation nor loudness establishes semantic setup or payoff.
    Mutates candidate exactly as the existing pipeline expects.
    """
    candidate.validate(duration_ms)
    if (type(max_length_ms) is not int or type(min_length_ms) is not int
            or not 0 < min_length_ms <= max_length_ms):
        raise ValueError("invalid clip length configuration")
    if type(context_ms) is not int or context_ms < 0:
        raise ValueError("context_ms must be a nonnegative integer")
    if type(audio_hop_ms) is not int or audio_hop_ms <= 0:
        raise ValueError("audio_hop_ms must be a positive integer")
    if audio_levels is not None and any(not isinstance(x, (int, float)) for x in audio_levels):
        raise TypeError("audio_levels must contain numeric dBFS readings")

    anchor = candidate.anchor_ms
    # Hard caps avoid turning continuous conversation into ~60-second clips.
    soft_cap = min(max_length_ms, _DEFAULT_SOFT_CAP_MS)
    pre_soft = min(8_000, context_ms + 2_000)
    post_soft = min(10_000, context_ms + 4_000)
    pre_hard = min(12_000, context_ms + 6_000)
    post_hard = min(16_000, context_ms + 10_000)

    # Source-relative time, not diarization; overlapping/alternating speakers
    # are treated equally. Read only locally relevant segments for expansion.
    nearby = []
    search_start = max(0, anchor - pre_hard - _EDGE_BRIDGE_MS)
    search_end = min(duration_ms, anchor + post_hard + _EDGE_BRIDGE_MS)
    for s in transcript:
        s.validate(duration_ms)
        if s.text.strip() and s.end_ms >= search_start and s.start_ms <= search_end:
            nearby.append(s)
    nearby.sort(key=lambda s: (s.start_ms, s.end_ms, s.speaker_id or ""))

    seed = min(range(len(nearby)), key=lambda i: (_distance_to_span(anchor, nearby[i]),
               abs((nearby[i].start_ms + nearby[i].end_ms) // 2 - anchor), i), default=None)
    supported = seed is not None and _distance_to_span(anchor, nearby[seed]) <= _SEED_DISTANCE_MS

    if supported:
        first = last = seed
        # Adjacent segment timing is the only evidence for context. The
        # one longer pause can connect an adjacent setup/payoff.
        bridged_before = False
        while first > 0:
            prev, selected = nearby[first - 1], nearby[first]
            gap = max(0, selected.start_ms - prev.end_ms)
            short_join = gap <= _PAUSE_JOIN_MS and prev.start_ms >= anchor - pre_soft
            # One longer pause may separate the context from a reaction.
            # This is proximity-based, NEVER a semantic relation claim.
            long_bridge = (not bridged_before and _PAUSE_JOIN_MS < gap <= _EDGE_BRIDGE_MS
                           and prev.start_ms >= anchor - pre_hard
                           and prev.end_ms >= anchor - 5_000)
            if not (short_join or long_bridge):
                break
            bridged_before |= long_bridge
            first -= 1
        bridged_after = False
        while last + 1 < len(nearby):
            following, selected = nearby[last + 1], nearby[last]
            gap = max(0, following.start_ms - selected.end_ms)
            short_join = gap <= _PAUSE_JOIN_MS and following.end_ms <= anchor + post_soft
            long_bridge = (not bridged_after and _PAUSE_JOIN_MS < gap <= _EDGE_BRIDGE_MS
                           and following.end_ms <= anchor + post_hard
                           and following.start_ms <= anchor + 5_000)
            if not (short_join or long_bridge):
                break
            bridged_after |= long_bridge
            last += 1

        selected = nearby[first:last + 1]
        start = max(0, min(s.start_ms for s in selected) - 350)
        end = min(duration_ms, max(s.end_ms for s in selected) + 650)
        # Keep the event even when the nearest segment is a little after/before.
        start = min(start, anchor)
        end = max(end, anchor + 1) if anchor < duration_ms else end
        start, end = _cap_around_anchor(start, end, anchor, duration_ms, soft_cap)
        hard_cut = (start > min(s.start_ms for s in selected) or
                    end < max(s.end_ms for s in selected))
        method = "transcript_assisted_hard_cut" if hard_cut else "transcript_assisted_bounded"
        note = ("ASR segment too long for duration cap; boundary may split speech. " if hard_cut else
                "Joined anchor-near speech segments using limited pauses and local context. ")
        note += "Timing heuristic, NOT semantic setup/payoff detection; human review required."
    else:
        # No useful ASR evidence. Keep a bounded portion of the discovery span,
        # rather than expanding a sparse event into an arbitrary full minute.
        cap = min(max_length_ms, _NO_TRANSCRIPT_CAP_MS)
        start, end = _cap_around_anchor(candidate.start_ms, candidate.end_ms,
                                        anchor, duration_ms, cap)
        method = "signal_padding_fallback"
        note = "No anchor-near transcript; conservative timing-only fallback. Human review required."
        if audio_levels is not None:
            start, end, snapped = _silence_snap(start, end, anchor, audio_levels,
                                                audio_hop_ms, duration_ms)
            if snapped:
                method = "signal_padding_audio_silence"
                note = "Fallback aligned to nearby quiet dBFS bins, not detected events; human review required."
        start, end = _cap_around_anchor(start, end, anchor, duration_ms, cap)

    start, end = _pad_to_minimum(start, end, anchor, duration_ms, min_length_ms)
    # A minimum longer than the soft cap is an explicit caller choice.
    if end - start > max_length_ms:
        start, end = _cap_around_anchor(start, end, anchor, duration_ms, max_length_ms)
    checked_span(start, end, duration_ms)
    candidate.start_ms, candidate.end_ms = start, end
    chronological = sorted((s for s in transcript if s.end_ms > start and s.start_ms < end),
                           key=lambda s: (s.start_ms, s.end_ms, s.speaker_id or ""))
    candidate.transcript_excerpt = nearby_text(chronological, start, end)
    candidate.validate(duration_ms)
    return BoundaryDecision(start, end, method, note)
