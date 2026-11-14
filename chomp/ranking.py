"""Transparent provisional ranking; model/agent providers intentionally deferred."""
from __future__ import annotations

from .models import Candidate

SCORE_KEYS = ("category_payoff", "standalone_clarity", "hook_strength", "distinctiveness")
CATEGORIES = {"funny", "surprise", "fail", "dispute", "discovery", "informative", "emotional", "other"}


def quality(scores: dict[str, float]) -> float:
    for k in SCORE_KEYS:
        if k not in scores or not 0 <= float(scores[k]) <= 5:
            raise ValueError(f"invalid {k} 0-5")
    return round(20 * (.4*scores["category_payoff"] + .3*scores["standalone_clarity"] + .2*scores["hook_strength"] + .1*scores["distinctiveness"]), 2)


def heuristic_rank(candidate: Candidate) -> Candidate:
    """NOT semantic AI. A transparent baseline to compare with human judgments and future providers."""
    text = candidate.transcript_excerpt
    words = len(text.split())
    strength = max((o.strength for o in candidate.observations), default=0)
    modalities = len({o.modality for o in candidate.observations if o.modality != "audit"})
    scores = {
        "category_payoff": round(min(4., .8 + strength*3), 2),
        "standalone_clarity": round(min(4., .5 + words/35), 2),
        "hook_strength": round(min(4., 1 + text.count("?")*.5 + text.count("!")*.3 + strength), 2),
        "distinctiveness": round(min(4., 1 + modalities*.6), 2),
    }
    candidate.scores = scores
    candidate.score = quality(scores)
    candidate.categories = ["other"]
    candidate.ranking_method = "heuristic_provisional"
    candidate.rationale = "Audio, speech density and chat signals only; human content review required."
    return candidate


def overlap_ratio(a: Candidate, b: Candidate) -> float:
    """Overlap relative to the *shorter* clip, not the union.

    Wide context windows can have low union IoU while still recycling a large
    portion of the shorter clip. Such near-repeats should not fill a top-3.
    """
    common = max(0, min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms))
    shorter = min(a.end_ms-a.start_ms, b.end_ms-b.start_ms)
    return common / shorter if shorter > 0 else 0


def select(candidates: list[Candidate], max_clips: int=3, min_score: float=45, overlap_threshold: float=.25) -> list[Candidate]:
    """Rank FIRST, suppress overlapping lower-ranked duplicates second, allow zero clips."""
    result: list[Candidate] = []
    for c in sorted(candidates, key=lambda x: x.score, reverse=True):
        if c.score < min_score:
            continue
        if any(overlap_ratio(c, chosen) > overlap_threshold or abs(c.anchor_ms-chosen.anchor_ms) < 12000 for chosen in result):
            continue
        result.append(c)
        if len(result) >= max_clips:
            break
    return result
