"""Source-clock evidence and shared contracts for the Director's editorial pass."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from itertools import accumulate
from typing import Sequence

from ..models import TranscriptSegment


def object_schema(**fields: dict) -> dict:
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


def array(items: dict) -> dict:
    return {"type": "array", "items": items}


TEXT = {"type": "string"}
IDS = array(TEXT)
NULLABLE_TEXT = {"type": ["string", "null"]}
SCAN_SCHEMA = object_schema(
    proposals=array(object_schema(event=TEXT, viewer_value=TEXT, evidence_ids=IDS)),
    leads=array(object_schema(note=TEXT, evidence_ids=IDS)),
)
DECISION_SCHEMA = object_schema(
    requests=array(object_schema(start_ms={"type": "integer"}, end_ms={"type": "integer"}, reason=TEXT)),
    decisions=array(object_schema(
        candidate_id=TEXT, verdict={"type": "string", "enum": ["select", "reject", "abstain"]},
        event=TEXT, viewer_value=TEXT, reason=TEXT, audience=TEXT, campaign_fit=TEXT,
        confidence={"type": "string", "enum": ["low", "medium", "high"]},
        evidence_ids=IDS, setup_ids=IDS, development_ids=IDS, payoff_ids=IDS,
        title_suggestion=NULLABLE_TEXT, hook_suggestion=NULLABLE_TEXT,
    )),
)


class TranscriptIndex:
    def __init__(self, segments: Sequence[TranscriptSegment]):
        self.by_id = {f"u{i}": s for i, s in enumerate(segments, 1) if s.text.strip()}
        self.ids = list(self.by_id)
        self.starts = [s.start_ms for s in self.by_id.values()]
        self.max_ends = list(accumulate((s.end_ms for s in self.by_id.values()), max))

    def ids_in(self, start: int, end: int) -> list[str]:
        lo, hi = bisect_right(self.max_ends, start), bisect_left(self.starts, end)
        return [uid for uid in self.ids[lo:hi] if self.by_id[uid].end_ms > start]

    def rows(self, ids: Sequence[str]) -> list[dict]:
        return [{"id": uid, "start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text,
                 "speaker_id": s.speaker_id} for uid in ids for s in (self.by_id[uid],)]

    def gaps(self, start: int, end: int) -> list[list[int]]:
        gaps = []
        cursor = start
        for uid in self.ids_in(start, end):
            s = self.by_id[uid]
            if cursor < s.start_ms:
                gaps.append([cursor, s.start_ms])
            cursor = max(cursor, s.end_ms)
        if cursor < end:
            gaps.append([cursor, end])
        return gaps

    def containing_span(self, ids: Sequence[str]) -> tuple[int, int]:
        start = min(self.by_id[uid].start_ms for uid in ids)
        end = max(self.by_id[uid].end_ms for uid in ids)
        while True:
            expanded = self.ids_in(start, end)
            left = min(self.by_id[uid].start_ms for uid in expanded)
            right = max(self.by_id[uid].end_ms for uid in expanded)
            if (left, right) == (start, end):
                return left, right
            start, end = left, right


def validate_refs(ids: list[str], allowed: set[str], *, required: bool = True) -> None:
    if required and not ids:
        raise ValueError("Required utterance evidence is missing")
    if len(ids) != len(set(ids)) or not allowed.issuperset(ids):
        raise ValueError("Repeated or unknown utterance IDs")


def has_text(*values: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError("Expected a nonempty editorial explanation")
