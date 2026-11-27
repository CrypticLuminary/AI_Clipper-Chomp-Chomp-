"""Candidate-specific context relations and contiguous source-clock closure."""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from .boundaries import BoundaryDecision, _silence_snap
from .evolution_memory import Memory, terms

QUALIFICATION = re.compile(r"^\W*(?:but\b|however\b|unless\b|only if\b|except\b|to be clear\b|actually\b|i (?:do not|don't) mean\b|i mean\b|that said\b|i was (?:joking|wrong)\b|correction\b)", re.I)
DEPENDENT = re.compile(r"^\W*(?:he\b|she\b|they\b|it\b|that\b|this\b|those\b|then\b|because\b|which\b|and then\b|turns out\b|remember when\b)", re.I)
REFERENTIAL = re.compile(r"\b(?:opened|picked up|found|grabbed|chose) (?:it|one|them)\b", re.I)
RELATIONS = ["setup", "payoff", "explanation", "contradiction", "qualification", "callback"]


@dataclass(frozen=True)
class Proposal:
    lane: str
    core_ids: tuple[str, ...]
    start_ms: int
    end_ms: int
    strength: float
    explanation: str
    ranks: dict[str, int] | None = None

    @property
    def anchor(self) -> int:
        return (self.start_ms + self.end_ms) // 2


def needs_setup(text: str) -> bool:
    return bool(DEPENDENT.search(text) or REFERENTIAL.search(text))


def investigate(memory: Memory, proposal: Proposal, search: bool, hybrid: bool) -> dict:
    core = memory.hydrate(proposal.core_ids)
    spoken = [row for row in core if row["modality"] == "utterance"]
    dialogue = " ".join(row["text"] for row in spoken)
    context = memory.around(proposal.start_ms, proposal.end_ms, margin=5500)
    inside = {row["id"] for row in context}
    query_terms = sorted(set(terms(dialogue)), key=lambda token: (len(memory.postings[token]), token))[:9]
    queries = []
    for scope, anchor in (("past", proposal.start_ms), ("future", proposal.end_ms)):
        if search and query_terms:
            queries.append({"scope": scope, "query": " ".join(query_terms), "reason": "Find setup or later qualification",
                            "hits": memory.search(" ".join(query_terms), scope=scope, anchor=anchor,
                                                  exclude=inside, limit=3, hybrid=hybrid)})
    following = [row for row in context if row["start_ms"] >= proposal.end_ms]
    first_speaker = spoken[0]["speaker_id"] if spoken else None
    last_speaker = spoken[-1]["speaker_id"] if spoken else None
    essential = []
    relationships = []
    risks = []

    def relate(row: dict, kind: str, needed: bool):
        relationships.append({"kind": kind, "evidence_id": row["id"],
                              "direction": "before" if row["end_ms"] <= proposal.start_ms else "after",
                              "role": "viewer" if needed else "interpretation", "basis": "local_rule"})
        if needed:
            essential.append(row["id"])

    for successor in following:
        if successor["speaker_id"] != last_speaker or not QUALIFICATION.search(successor["text"]):
            break
        if spoken:
            relate(successor, "qualification", True)
    if spoken and needs_setup(spoken[0]["text"]):
        cursor = proposal.start_ms
        resolved = False
        preceding = memory.around(max(0, proposal.start_ms - 16_000), proposal.start_ms)
        for earlier in reversed([r for r in preceding if r["end_ms"] <= proposal.start_ms][-6:]):
            if cursor - earlier["end_ms"] > 5500 or earlier["speaker_id"] != first_speaker:
                break
            relate(earlier, "setup", True)
            cursor = earlier["start_ms"]
            if not needs_setup(earlier["text"]) and not re.search(r"\b(?:their|his|her)\b", earlier["text"], re.I):
                resolved = True
                break
        if not resolved:
            risks.append("unresolved_reference")
    if spoken and not re.search(r"""[.!?]["'”’)]*\s*$""", spoken[-1]["text"]):
        risks.append("uncertain_sentence_end")
    for request in queries:
        for hit in request["hits"]:
            row = memory.rows[hit["evidence_id"]]
            shared = set(terms(row["text"])) & set(query_terms)
            if len(shared) < 2:
                continue
            if request["scope"] == "future" and QUALIFICATION.search(row["text"]) and row["speaker_id"] == last_speaker:
                relate(row, "qualification", False)
                risks.append("possible_later_qualification")
            if request["scope"] == "past" and "unresolved_reference" in risks:
                relate(row, "callback", False)
    return {"preliminary_interpretation": dialogue or proposal.explanation, "potential_viewer_value": proposal.explanation,
            "queries": queries, "local_context_ids": [r["id"] for r in context], "relationships": relationships,
            "viewer_required_groups": [list(dict.fromkeys(essential))],
            "interpretation_only_ids": list(dict.fromkeys(r["evidence_id"] for r in relationships
                                                         if r["role"] == "interpretation")),
            "risks": sorted(set(risks)), "stop_reason": "local_evidence_pass_complete"}


def close(memory: Memory, proposal: Proposal, groups: list[list[str]], *, duration: int,
          minimum: int, maximum: int, audio: list[float], hop: int, snap: bool) -> tuple[BoundaryDecision | None, list[dict]]:
    """Each group is an alternative sufficient set, with mandatory core in every interval."""
    options = []
    for group in groups:
        rows = memory.hydrate([*proposal.core_ids, *group])
        start = min([proposal.start_ms, *(r["start_ms"] for r in rows)])
        end = max([proposal.end_ms, *(r["end_ms"] for r in rows)])
        required_start, required_end = start, end
        nearby = memory.around(max(0, start - 2500), min(duration, end + 2500))
        starts = {start, *(r["start_ms"] for r in nearby if r["start_ms"] <= start)}
        ends = {end, *(r["end_ms"] for r in nearby if r["end_ms"] >= end)}
        if end - start < minimum:
            missing = minimum - (end - start)
            padded_start = max(0, min(start - missing // 2, duration - minimum))
            starts.add(padded_start)
            ends.add(padded_start + minimum)
        candidates = []
        for left in sorted(starts, reverse=True)[:16]:
            for right in sorted(ends)[:16]:
                if not 0 <= left <= required_start < required_end <= right <= duration:
                    continue
                if not minimum <= right - left <= maximum:
                    continue
                if any(r["start_ms"] < left < r["end_ms"] or r["start_ms"] < right < r["end_ms"] for r in nearby):
                    continue
                candidates.append((left, right))
        if not candidates:
            options.append({"required_ids": group, "state": "no_contiguous_interval", "required_span_ms": [start, end]})
            continue
        left, right = min(candidates, key=lambda t: (t[1] - t[0], t[0]))
        adjusted = _silence_snap(left, right, proposal.anchor, audio, hop, duration) if snap else (left, right, False)
        x, y, changed = adjusted
        # Only retain technical snapping in speech-free gaps and recheck every hard bound.
        safe = (0 <= x <= required_start < required_end <= y <= duration and minimum <= y - x <= maximum
                and not any(r["start_ms"] < x < r["end_ms"] or r["start_ms"] < y < r["end_ms"]
                            for r in memory.around(x - 1, y + 1))
                and [r["id"] for r in memory.around(x, y)] == [r["id"] for r in memory.around(left, right)])
        options.append({"required_ids": group, "state": "feasible", "semantic_span_ms": [left, right],
                        "final_span_ms": [x, y] if changed and safe else [left, right],
                        "technical_snap": "retained" if changed and safe else "discarded" if changed else "unchanged"})
    feasible = [o for o in options if o["state"] == "feasible"]
    if not feasible:
        return None, options
    selected = min(feasible, key=lambda o: (o["semantic_span_ms"][1] - o["semantic_span_ms"][0],
                                            o["semantic_span_ms"][0]))
    return BoundaryDecision(*selected["final_span_ms"], "evolution_context_closure",
                            "Core and one sufficient viewer-context group retained"), options


def repeated_story(left: str, right: str) -> bool:
    a, b = terms(left), terms(right)
    return min(len(a), len(b)) >= 6 and SequenceMatcher(None, a, b, autojunk=False).ratio() >= .78
