"""Compare machine proposals and exports against independent source annotations."""
from __future__ import annotations

import json
from pathlib import Path

from ..artifacts import write_json
from ..models import checked_span
from .core import ClipLedgerError, evaluate, read_annotations, read_manifest

EVENT_SCHEMA = "chomp-source-events/1"


def _document(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ClipLedgerError(f"{path.name} must contain a JSON object")
    return data


def create_event_template(ledger_path: Path, out: Path, *, reviewer: str) -> dict:
    # Only the source identity and clock are copied into the annotation file.
    ledger = _document(ledger_path)
    if ledger.get("schema_version") != "chomp-candidates/1" or not reviewer.strip():
        raise ClipLedgerError("A candidate ledger and reviewer are required")
    template = {"schema_version": EVENT_SCHEMA, "source_id": ledger["source_id"],
                "source": ledger["source"], "duration_ms": ledger["duration_ms"],
                "reviewer": reviewer, "complete": False, "events": []}
    write_json(out, template, exclusive=True)
    return template


def _span(row: dict, key: str, duration: int) -> tuple[int, int]:
    values = row.get(key)
    if not isinstance(values, list) or len(values) != 2:
        raise ClipLedgerError(f"{key} must be a pair of source milliseconds")
    a, b = values
    checked_span(a, b, duration)
    return a, b


def _edge_range(row: dict, key: str, duration: int) -> tuple[int, int]:
    values = row.get(key)
    if (not isinstance(values, list) or len(values) != 2 or any(type(x) is not int for x in values)
            or not 0 <= values[0] <= values[1] <= duration):
        raise ClipLedgerError(f"{key} must be an ordered pair of source milliseconds")
    return values[0], values[1]


def _contains(interval: tuple[int, int], required: tuple[int, int]) -> bool:
    return interval[0] <= required[0] and required[1] <= interval[1]


def _covers_core(interval: tuple[int, int], core: tuple[int, int]) -> bool:
    overlap = min(interval[1], core[1]) - max(interval[0], core[0])
    return 5 * overlap >= 4 * (core[1] - core[0])


def _covered_ms(intervals: list[tuple[int, int]]) -> int:
    end = total = 0
    for a, b in sorted(intervals):
        total += max(0, b - max(a, end))
        end = max(end, b)
    return total


def benchmark(events_path: Path, ledger_path: Path, *, manifest_path: Path | None = None,
              reviews: tuple[Path, ...] = ()) -> dict:
    gold, ledger = _document(events_path), _document(ledger_path)
    if gold.get("schema_version") != EVENT_SCHEMA or ledger.get("schema_version") != "chomp-candidates/1":
        raise ClipLedgerError("Unsupported event or candidate ledger")
    duration = ledger["duration_ms"]
    source_id = ledger["source_id"]
    if (type(duration) is not int or duration <= 0 or not isinstance(source_id, str)
            or gold.get("source_id") != source_id or gold.get("duration_ms") != duration):
        raise ClipLedgerError("Event annotations and proposals must use the same source and clock")
    if type(gold.get("complete")) is not bool or not isinstance(gold.get("events"), list):
        raise ClipLedgerError("Event annotations need a complete boolean and events list")
    if not isinstance(gold.get("reviewer"), str) or not gold["reviewer"].strip():
        raise ClipLedgerError("Event annotations need a reviewer")
    if reviews and manifest_path is None:
        raise ClipLedgerError("Supply the review manifest with clip reviews")

    proposals, selected = [], []
    for row in ledger["candidates"]:
        proposal = row["proposal"]
        start, end = proposal["start_ms"], proposal["end_ms"]
        checked_span(start, end, duration)
        if end - start > 60_000:
            raise ClipLedgerError("Benchmark proposals must be at most 60 seconds")
        proposals.append((start, end))
        if row["decision"] == "selected":
            final = row["candidate"]
            start, end = final["start_ms"], final["end_ms"]
            checked_span(start, end, duration)
            selected.append((start, end))

    results = []
    seen = set()
    for event in gold["events"]:
        if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not event["id"].strip():
            raise ClipLedgerError("Every event needs a nonempty ID")
        event_id = event["id"]
        if event_id in seen:
            raise ClipLedgerError("Event IDs must be unique")
        seen.add(event_id)
        core = _span(event, "core_ms", duration)
        essential = _span(event, "essential_ms", duration)
        starts = _edge_range(event, "start_range_ms", duration)
        ends = _edge_range(event, "end_range_ms", duration)
        if not _contains(essential, core) or starts[1] > essential[0] or ends[0] < essential[1]:
            raise ClipLedgerError(f"{event_id} needs consistent core, essential span and admissible edges")
        hits = [pair for pair in selected if _covers_core(pair, core)]
        results.append({
            "id": event_id,
            "discovered": any(_covers_core(pair, core) for pair in proposals),
            "selected": bool(hits),
            "essential_preserved": any(_contains(pair, essential) for pair in hits),
            "boundaries_accepted": any(starts[0] <= a <= starts[1] and ends[0] <= b <= ends[1] for a, b in hits),
            "matching_selected": len(hits),
        })

    assessments = []
    if manifest_path is not None:
        manifest = read_manifest(manifest_path)
        if (manifest.source_id != source_id or manifest.job_id != ledger["job_id"]
                or manifest.duration_ms != duration
                or sorted((clip.start_ms, clip.end_ms) for clip in manifest.clips) != sorted(selected)):
            raise ClipLedgerError("Review manifest and candidate ledger refer to different runs")
        for path in reviews:
            annotation = read_annotations(manifest, path)
            assessments.append({"path": str(path), "metrics": evaluate(manifest, annotation)})

    count = len(results)
    recalled = sum(event["discovered"] for event in results)
    timing = ledger.get("timing") or {}
    assessment_costs = []
    for assessment in assessments:
        metrics = assessment["metrics"]
        good = metrics["precision_at_3"]["good"]
        complete = metrics["precision_at_3"]["value"] is not None
        seconds = timing.get("total_seconds")
        cost = timing.get("remote_api_cost")
        assessment_costs.append({
            "good": good, "reviewed_coverage": metrics["review_coverage"],
            "processing_seconds_per_positive_review": seconds / good if complete and good and seconds is not None else None,
            "api_cost_per_positive_review": cost / good if complete and good and cost is not None else None,
        })
    return {
        "schema_version": "chomp-event-benchmark/1", "source_id": source_id, "job_id": ledger["job_id"],
        "complete_source_annotation": gold["complete"], "events": results,
        "annotated_events": count, "discovered_annotated_events": recalled,
        "proposal_recall": recalled / count if gold["complete"] and count else None,
        "selected_count": len(selected), "proposals": len(proposals),
        "proposals_per_source_hour": round(len(proposals) * 3_600_000 / duration, 2),
        "proposal_minutes_per_source_hour": round(_covered_ms(proposals) * 60 / duration, 2),
        "correctly_empty_on_negative_source": not selected if gold["complete"] and count == 0 else None,
        "timing": timing, "clip_reviews": assessments, "acceptance_costs": assessment_costs,
    }
