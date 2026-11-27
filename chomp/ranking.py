"""Transparent baseline scoring and diversity-aware selection."""
from __future__ import annotations

import re

from .models import Candidate
from .clipcontrol import MAX_CLIPS

SCORE_KEYS = ("category_payoff", "standalone_clarity", "hook_strength", "distinctiveness")
CATEGORIES = {"funny", "surprise", "fail", "dispute", "discovery", "informative", "emotional", "other"}


def quality(scores: dict[str, float]) -> float:
    for k in SCORE_KEYS:
        if k not in scores or type(scores[k]) not in (int, float) or not 0 <= scores[k] <= 5:
            raise ValueError(f"invalid {k} 0-5")
    return round(20 * (.4*scores["category_payoff"] + .3*scores["standalone_clarity"] + .2*scores["hook_strength"] + .1*scores["distinctiveness"]), 2)


def heuristic_rank(candidate: Candidate) -> Candidate:
    """NOT semantic AI. A transparent baseline to compare with human judgments and future providers."""
    text = candidate.transcript_excerpt
    words = len(text.split())
    strengths = [o.strength for o in candidate.observations if o.modality in {"audio", "transcript"}]
    # Chat can suggest a window, but missing chat is neutral for clip eligibility.
    strength = max(strengths, default=0)
    scores = {
        "category_payoff": round(min(4.4, .8 + strength*3.6), 2),
        "standalone_clarity": round(min(4., .5 + words/35), 2),
        "hook_strength": round(min(4., 1 + text.count("?")*.5 + text.count("!")*.3 + strength), 2),
        "distinctiveness": 1.6,
    }
    candidate.scores = scores
    candidate.score = quality(scores)
    candidate.categories = ["other"]
    candidate.ranking_method = "heuristic_provisional"
    candidate.rationale = "Provisional signal score. Review the complete moment and visual evidence."
    return candidate


def overlap_ratio(a: Candidate, b: Candidate) -> float:
    """Overlap relative to the *shorter* clip, not the union.

    Wide context windows can have low union IoU while still recycling a large
    portion of the shorter clip. Such near-repeats should not fill a top-3.
    """
    common = max(0, min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms))
    shorter = min(a.end_ms-a.start_ms, b.end_ms-b.start_ms)
    return common / shorter if shorter > 0 else 0


def _repeated_text(a: Candidate, b: Candidate) -> bool:
    def phrases(candidate: Candidate) -> set[tuple[str, ...]]:
        tokens = re.findall(r"\w+", candidate.transcript_excerpt.casefold())
        return set(zip(tokens, tokens[1:], tokens[2:]))

    left, right = phrases(a), phrases(b)
    return min(len(left), len(right)) >= 6 and len(left & right) / len(left | right) >= .68


def duplicate(a: Candidate, b: Candidate, overlap_threshold: float = .25) -> bool:
    return overlap_ratio(a, b) > overlap_threshold or _repeated_text(a, b)


def select(candidates: list[Candidate], max_clips: int=3, min_score: float=45, overlap_threshold: float=.25) -> list[Candidate]:
    """Rank FIRST, suppress overlapping lower-ranked duplicates second, allow zero clips."""
    if type(max_clips) is not int or not 0 <= max_clips <= MAX_CLIPS:
        raise ValueError(f"Choose between zero and {MAX_CLIPS} review clips")
    if max_clips == 0:
        return []
    if not 0 <= min_score <= 100 or any(not 0 <= candidate.score <= 100 for candidate in candidates):
        raise ValueError("ranking scores and the minimum score must be finite and within 0..100")
    result: list[Candidate] = []
    for c in sorted(candidates, key=lambda x: x.score, reverse=True):
        if c.score < min_score:
            continue
        if any(duplicate(c, chosen, overlap_threshold) for chosen in result):
            continue
        result.append(c)
        if len(result) >= max_clips:
            break
    return result
