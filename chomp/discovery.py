"""Source-clock proposal generation with a budget shared across the VOD."""
from __future__ import annotations

from collections import defaultdict, deque
import math

from .models import Candidate, Observation, TranscriptSegment
from .signals import nearby_text


def discover(observations: list[Observation], words: list[TranscriptSegment], duration_ms: int, *,
             before_ms: int = 9000, after_ms: int = 14000, max_proposals: int | None = None,
             proposals_per_hour: int = 40) -> list[Candidate]:
    if duration_ms <= 0 or type(proposals_per_hour) is not int or not 1 <= proposals_per_hour <= 240:
        raise ValueError("A positive duration and a proposal budget of 1–240 per hour are required")
    budget = max(4, math.ceil(duration_ms * proposals_per_hour / 3_600_000)) if max_proposals is None else max_proposals
    if type(budget) is not int or budget < 0:
        raise ValueError("Proposal count must be nonnegative")
    if budget == 0:
        return []

    groups: list[Candidate] = []
    for obs in sorted(observations, key=lambda o: (o.start_ms + o.end_ms, o.modality)):
        obs.validate(duration_ms)
        anchor = (obs.start_ms + obs.end_ms) // 2
        start, end = max(0, anchor - before_ms), min(duration_ms, anchor + after_ms)
        if end <= start:
            continue
        # Group matching event cores without swallowing nearby separate moments.
        if groups and anchor - groups[-1].anchor_ms <= 2600:
            previous = groups[-1]
            previous.observations.append(obs)
            previous.start_ms = min(previous.start_ms, start)
            previous.end_ms = max(previous.end_ms, end)
        else:
            groups.append(Candidate(start, end, anchor, [obs]))

    def strength(candidate: Candidate) -> float:
        return max(o.strength for o in candidate.observations)

    # Interleave five-minute ranges, then evidence lanes within each range.
    # Quiet speech therefore competes for review time even during noisy sources.
    by_time: dict[int, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))
    for candidate in sorted(groups, key=lambda c: (-strength(c), c.anchor_ms)):
        lane = max(candidate.observations, key=lambda o: o.strength).modality
        by_time[candidate.anchor_ms // 300_000][lane].append(candidate)
    queues = []
    for _, lanes in sorted(by_time.items()):
        lane_queues = sorted(lanes.values(), key=lambda candidates: -strength(candidates[0]))
        interleaved = deque()
        while lane_queues:
            for queue in lane_queues:
                interleaved.append(queue.popleft())
            lane_queues = [queue for queue in lane_queues if queue]
        queues.append(interleaved)

    selected = []
    while queues and len(selected) < budget:
        # When the budget is smaller than the number of time blocks, start
        # evenly across that range instead of always favoring early footage.
        remaining = budget - len(selected)
        if remaining < len(queues):
            queues = [queues[((2 * i + 1) * len(queues)) // (2 * remaining)] for i in range(remaining)]
        for queue in queues:
            selected.append(queue.popleft())
        queues = [queue for queue in queues if queue]

    for candidate in selected:
        candidate.transcript_excerpt = nearby_text(words, max(0, candidate.start_ms - 15_000),
                                                   min(duration_ms, candidate.end_ms + 15_000))
        candidate.validate(duration_ms)
    return selected
