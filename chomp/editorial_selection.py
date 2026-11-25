"""Additive reader for existing editorial reports; no replacement decision model.

The production and benchmark adapters share this source-clock validation boundary.
Original Director rows are retained verbatim for provenance and migration.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .models import checked_span


def text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def span(value, duration, field="interval"):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{field} must contain two source milliseconds")
    checked_span(*value, duration)
    return list(value)


def _object(value, field):
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _ids(value, field):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{field} must be an ID array")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicate IDs")
    return value


def _header(data):
    _object(data, "editorial report")
    for field in ("source_id", "source", "job_id"):
        text(data.get(field), field)
    duration = data.get("duration_ms")
    if type(duration) is not int or duration <= 0:
        raise ValueError("duration_ms must be a positive integer")
    if not isinstance(data.get("candidates"), list):
        raise ValueError("candidates must be an array")
    return duration


def adapt_director_report(data: dict) -> dict:
    """Validate director-selection/1 and lower it to chomp-candidates/1.

    Abstentions remain abstentions; no semantic scores or reviewer judgments
    are synthesized. Production performs additional source/evidence verification.
    """
    duration = _header(data)
    if data.get("schema_version") != "director-selection/1":
        raise ValueError("Expected director-selection/1")
    section = span(data.get("section_ms"), duration, "section_ms")
    if not isinstance(data.get("decisions"), list):
        raise ValueError("decisions must be an array")
    candidates = {}
    for proposal in data["candidates"]:
        _object(proposal, "proposal")
        cid = text(proposal.get("candidate_id"), "candidate_id")
        if cid in candidates:
            raise ValueError("Repeated candidate ID")
        left, right = span(proposal.get("range_ms"), duration, "proposal.range_ms")
        if not section[0] <= left < right <= section[1]:
            raise ValueError("Proposal outside source section")
        _ids(proposal.get("evidence_ids", []), "proposal.evidence_ids")
        if not isinstance(proposal.get("lanes"), list) or not proposal["lanes"]:
            raise ValueError("Proposal lane provenance is required")
        if any(not isinstance(x, str) or not x.strip() for x in proposal["lanes"]):
            raise ValueError("Invalid proposal lane")
        candidates[cid] = proposal
    decisions = {}
    selected = 0
    for decision in data["decisions"]:
        _object(decision, "decision")
        cid = decision.get("candidate_id")
        if not isinstance(cid, str) or cid not in candidates or cid in decisions:
            raise ValueError("Repeated or unknown decision candidate")
        verdict = decision.get("verdict")
        if verdict not in ("select", "reject", "abstain"):
            raise ValueError("Invalid editorial verdict")
        text(decision.get("reason"), "decision.reason")
        refs = _ids(decision.get("evidence_ids", []), "decision.evidence_ids")
        evidence = decision.get("evidence", [])
        if not isinstance(evidence, list):
            raise ValueError("decision.evidence must be an array")
        known = set()
        for item in evidence:
            _object(item, "evidence item")
            eid = text(item.get("id"), "evidence.id")
            if eid in known:
                raise ValueError("Repeated evidence ID")
            known.add(eid)
            span([item.get("start_ms"), item.get("end_ms")], duration, "evidence interval")
            text(item.get("text"), "utterance text")
            if item.get("source_id", data["source_id"]) != data["source_id"]:
                raise ValueError("Evidence source mismatch")
        if not set(refs).issubset(known):
            raise ValueError("Missing referenced evidence")
        for field in ("setup_ids", "development_ids", "payoff_ids"):
            beats = _ids(decision.get(field, []), field)
            if not set(beats).issubset(refs):
                raise ValueError("Story beat references uncited evidence")
        boundary = decision.get("boundary")
        if boundary is not None:
            _object(boundary, "boundary")
            left, right = span([boundary.get("start_ms"), boundary.get("end_ms")], duration)
            if not section[0] <= left < right <= section[1] and verdict == "select":
                raise ValueError("Selected boundary outside source section")
        if verdict == "select":
            if boundary is None:
                raise ValueError("Selected decision requires a final boundary")
            if "heuristic_range_ms" not in decision and not refs:
                raise ValueError("Semantic selection requires grounded evidence")
            selected += 1
        decisions[cid] = decision
    if type(data.get("selected_count")) is not int or data["selected_count"] != selected or selected > 3:
        raise ValueError("Selected count is inconsistent or exceeds three")
    usage = data.get("usage") or {}
    _object(usage, "usage")
    provider_calls = usage.get("provider_calls")
    if provider_calls is not None and (type(provider_calls) is not int or provider_calls < 0):
        raise ValueError("Invalid provider call count")
    seconds = data.get("elapsed_seconds")
    if seconds is not None and (type(seconds) not in (float, int) or not math.isfinite(seconds) or seconds < 0):
        raise ValueError("Invalid processing time")
    result = {"schema_version": "chomp-candidates/1", "source_format": data["schema_version"],
              **{key: data[key] for key in ("source_id", "source", "job_id", "duration_ms")},
              "candidates": [], "timing": {"total_seconds": seconds, "remote_api_cost": usage.get("cost_usd"),
                                           "provider_calls": provider_calls},
              "coverage": deepcopy(data.get("coverage")), "availability": deepcopy(data.get("availability")),
              "provider_usage": deepcopy(usage), "review_status": data.get("review_status"),
              "analysis_status": data.get("status"), "director_report": deepcopy(data)}
    for cid, proposal in candidates.items():
        decision = decisions.get(cid, {"candidate_id": cid, "verdict": "abstain", "reason": "No editorial decision supplied."})
        boundary = decision.get("boundary")
        result["candidates"].append({
            "candidate_id": cid, "proposal": {"start_ms": proposal["range_ms"][0], "end_ms": proposal["range_ms"][1]},
            "candidate": ({"start_ms": boundary["start_ms"], "end_ms": boundary["end_ms"], "score": None} if boundary else None),
            "decision": "selected" if decision["verdict"] == "select" else decision["verdict"],
            "reason_detail": decision["reason"], "evidence_refs": deepcopy(decision.get("evidence_ids", [])),
            "lanes": deepcopy(proposal["lanes"]), "editorial": deepcopy(decision), "director_proposal": deepcopy(proposal),
        })
    return result


def read_selection(data: dict) -> dict:
    if isinstance(data, dict) and data.get("schema_version") == "director-selection/1":
        return adapt_director_report(data)
    duration = _header(data)
    if data.get("schema_version") != "chomp-candidates/1":
        raise ValueError("Unsupported editorial report schema")
    ids, count = set(), 0
    for row in data["candidates"]:
        _object(row, "candidate row")
        proposal = _object(row.get("proposal"), "proposal")
        span([proposal.get("start_ms"), proposal.get("end_ms")], duration)
        text(row.get("decision"), "decision")
        cid = row.get("candidate_id")
        if cid is not None:
            text(cid, "candidate_id")
            if cid in ids:
                raise ValueError("Repeated candidate ID")
            ids.add(cid)
        if row["decision"] == "selected":
            final = _object(row.get("candidate"), "selected candidate")
            span([final.get("start_ms"), final.get("end_ms")], duration)
            count += 1
    if count > 3:
        raise ValueError("At most three distinct review clips are supported")
    return deepcopy(data)
