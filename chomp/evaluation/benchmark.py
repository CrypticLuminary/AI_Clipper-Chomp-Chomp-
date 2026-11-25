"""Compare machine proposals and exports against independent source annotations."""
from __future__ import annotations

import json
import math
from pathlib import Path

from ..artifacts import write_json
from ..editorial_selection import read_selection
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
    ledger = read_selection(_document(ledger_path))
    if not reviewer.strip():
        raise ClipLedgerError("A candidate ledger and reviewer are required")
    template = {"schema_version": EVENT_SCHEMA, "source_id": ledger["source_id"],
                "source": ledger["source"], "duration_ms": ledger["duration_ms"],
                "reviewer": reviewer, "complete": False, "events": []}
    write_json(out, template, exclusive=True)
    return template


def validate_events(gold: dict) -> dict:
    """Validate the existing v1 spans plus optional ChompBench-1 metadata."""
    if not isinstance(gold, dict) or gold.get("schema_version") != EVENT_SCHEMA:
        raise ClipLedgerError("Unsupported source-event annotation schema")
    duration = gold.get("duration_ms")
    if type(duration) is not int or duration <= 0:
        raise ClipLedgerError("Event duration must be positive integer milliseconds")
    if not isinstance(gold.get("source_id"), str) or not gold["source_id"].strip():
        raise ClipLedgerError("Events require a source_id")
    if type(gold.get("complete")) is not bool or not isinstance(gold.get("events"), list):
        raise ClipLedgerError("Event annotations need a complete boolean and events list")
    if not isinstance(gold.get("reviewer"), str) or not gold["reviewer"].strip():
        raise ClipLedgerError("Event annotations need a reviewer")
    if gold.get("benchmark_name") == "ChompBench-1":
        for field in ("creator_id", "content_category", "rights_reference"):
            if not isinstance(gold.get(field), str) or not gold[field].strip():
                raise ClipLedgerError(f"ChompBench-1 requires {field}")
        if gold.get("dataset_split") not in {"development", "holdout"}:
            raise ClipLedgerError("Invalid dataset split")
        if gold.get("annotation_origin") not in {"human", "synthetic", "ai"}:
            raise ClipLedgerError("Specify human, synthetic or ai annotation_origin")
        if gold.get("annotation_completeness") != ("complete" if gold["complete"] else "incomplete"):
            raise ClipLedgerError("Annotation completeness disagrees with complete flag")
        if gold["complete"] and gold["annotation_origin"] == "ai":
            raise ClipLedgerError("AI annotations cannot establish complete independent source judgments")
    seen = set()
    for event in gold["events"]:
        if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not event["id"].strip():
            raise ClipLedgerError("Every event needs a nonempty ID")
        if event["id"] in seen:
            raise ClipLedgerError("Event IDs must be unique")
        seen.add(event["id"])
        core = _span(event, "core_ms", duration)
        essential = _span(event, "essential_ms", duration)
        starts = _edge_range(event, "start_range_ms", duration)
        ends = _edge_range(event, "end_range_ms", duration)
        if not _contains(essential, core) or starts[1] > essential[0] or ends[0] < essential[1]:
            raise ClipLedgerError("Inconsistent core, essential span and admissible edges")
        if gold.get("benchmark_name") == "ChompBench-1":
            for field in ("event_description", "highlight_category", "necessary_context", "viewer_value", "reviewer_id"):
                if not isinstance(event.get(field), str) or not event[field].strip():
                    raise ClipLedgerError(f"Event requires {field}")
            for field in ("required_audio_evidence", "required_visual_evidence"):
                if not isinstance(event.get(field), list) or any(not isinstance(x, str) for x in event[field]):
                    raise ClipLedgerError(f"{field} must be a string array")
            if event.get("review_status") not in {"pending", "reviewed", "disputed"}:
                raise ClipLedgerError("Invalid event review_status")
            if gold["complete"] and event["review_status"] != "reviewed":
                raise ClipLedgerError("Complete sources cannot contain pending/disputed events")
    return gold


def validate_dataset(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("schema_version") != "chompbench-dataset/1" or not isinstance(data.get("sources"), list):
        raise ClipLedgerError("Expected chompbench-dataset/1 sources")
    sources, creators = set(), {}
    for row in data["sources"]:
        if not isinstance(row, dict):
            raise ClipLedgerError("Dataset source must be an object")
        for field in ("source_id", "creator_id", "content_category", "rights_reference"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ClipLedgerError(f"Dataset source requires {field}")
        if row["source_id"] in sources:
            raise ClipLedgerError("Duplicate dataset source")
        sources.add(row["source_id"])
        split = row.get("dataset_split")
        if split not in {"development", "holdout"}:
            raise ClipLedgerError("Invalid dataset split")
        if row["creator_id"] in creators and creators[row["creator_id"]] != split:
            raise ClipLedgerError("Creator leakage across development and holdout")
        creators[row["creator_id"]] = split
        if type(row.get("duration_ms")) is not int or row["duration_ms"] <= 0:
            raise ClipLedgerError("Invalid source duration")
    return {"valid": True, "source_count": len(sources), "creator_count": len(creators),
            "target_sources": 24, "complete_dataset": len(sources) >= 24}


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
              reviews: tuple[Path, ...] = (), technical_path: Path | None = None) -> dict:
    gold, ledger = validate_events(_document(events_path)), read_selection(_document(ledger_path))
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
        # Discovery proposals may contain long setups. Campaign limits apply
        # to final selections, not to a benchmark's discovery representation.
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
    selected_hits = [event for event in results if event["selected"]]
    duplicate_indices = set()
    for event in gold["events"]:
        core = tuple(event["core_ms"])
        hits = [i for i, interval in enumerate(selected) if _covers_core(interval, core)]
        duplicate_indices.update(hits[1:])
    analysis_complete = ledger.get("analysis_status") in (None, "review_pending")
    # A section-only or interrupted analysis cannot establish a correct empty
    # decision for the entire annotated source.
    original_director = ledger.get("director_report")
    if original_director:
        analysis_complete = (analysis_complete and original_director["section_ms"] == [0, duration]
                             and not original_director.get("discovery_warning"))
    measured = lambda numerator, denominator: {"numerator": numerator, "denominator": denominator,
                                              "value": numerator / denominator if denominator else None}
    unavailable = {key: {"value": None, "reason": "measurement_not_supplied"} for key in (
        "human_approved_clip_precision", "selected_clip_precision", "caption_overflow", "missing_audio",
        "silent_audio", "av_sync_error_ms", "streamer_retention", "protected_region_visibility",
        "camera_motion_stability", "peak_memory_bytes")}
    usage = ledger.get("provider_usage") or {}
    records = usage.get("calls", [])
    cache_hits = usage.get("cache_hits")
    metrics = {
        "proposal_recall": measured(recalled, count) if gold["complete"] else
            {"numerator": recalled, "denominator": count, "value": None, "reason": "incomplete_source_annotations"},
        "setup_payoff_completeness": measured(sum(x["essential_preserved"] for x in selected_hits), len(selected_hits)),
        "acceptable_boundary_rate": measured(sum(x["boundaries_accepted"] for x in selected_hits), len(selected_hits)),
        "duplicate_selection_rate": measured(len(duplicate_indices), len(selected)),
        "correct_empty_result_rate": measured(int(not selected), 1) if gold["complete"] and not count and analysis_complete else measured(0, 0),
        "cache_hit_rate": measured(cache_hits, len(records)) if cache_hits is not None else measured(0, 0),
        **unavailable,
    }
    if not gold["complete"] or not count:
        metrics["duplicate_selection_rate"]["value"] = None
        metrics["duplicate_selection_rate"]["reason"] = "insufficient_annotated_event_identity"
    technical = None
    if technical_path is not None:
        technical = _document(technical_path)
        if (technical.get("schema_version") != "chompbench-technical/1" or technical.get("source_id") != source_id
                or technical.get("job_id") != ledger["job_id"] or technical.get("duration_ms") != duration
                or not isinstance(technical.get("clips"), list)):
            raise ClipLedgerError("Technical measurements refer to a different source/run")
        expected = {row.get("candidate_id"): (row["candidate"]["start_ms"], row["candidate"]["end_ms"])
                    for row in ledger["candidates"] if row["decision"] == "selected"}
        seen, measured_rows = set(), []
        for clip in technical["clips"]:
            if not isinstance(clip, dict) or not isinstance(clip.get("candidate_id"), str):
                raise ClipLedgerError("Technical clip needs a candidate ID")
            cid = clip["candidate_id"]
            if cid in seen or cid not in expected or (clip.get("start_ms"), clip.get("end_ms")) != expected[cid]:
                raise ClipLedgerError("Technical clip source interval/identity mismatch")
            seen.add(cid)
            measurements = clip.get("measurements")
            if not isinstance(measurements, dict):
                raise ClipLedgerError("Technical clip needs measurements")
            measured_rows.append(measurements)
        for field in ("missing_audio", "silent_audio", "caption_overflow"):
            values = [row[field] for row in measured_rows if row.get(field) is not None]
            if any(type(value) is not bool for value in values):
                raise ClipLedgerError(f"{field} measurements must be boolean or null")
            metrics[field] = measured(sum(values), len(values))
        lags = [row["envelope_lag_ms"] for row in measured_rows if row.get("envelope_lag_ms") is not None]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in lags):
            raise ClipLedgerError("Sync measurements must be finite milliseconds")
        metrics["av_sync_error_ms"] = {"value": sum(abs(x) for x in lags) / len(lags) if lags else None,
                                        "denominator": len(lags), "method": "audio_envelope_relative_to_source_20ms"}
    if assessments:
        metrics["human_approved_clip_precision"] = [x["metrics"]["precision_at_3"] for x in assessments]
        metrics["selected_clip_precision"] = metrics["human_approved_clip_precision"]
    return {
        "schema_version": "chomp-event-benchmark/1", "source_id": source_id, "job_id": ledger["job_id"],
        "benchmark_name": "ChompBench-1", "annotation_origin": gold.get("annotation_origin", "unspecified_legacy"),
        "dataset_split": gold.get("dataset_split"), "analysis_status": ledger.get("analysis_status"),
        "coverage": ledger.get("coverage"), "availability": ledger.get("availability"), "metrics": metrics,
        "complete_source_annotation": gold["complete"], "events": results,
        "annotated_events": count, "discovered_annotated_events": recalled,
        "proposal_recall": recalled / count if gold["complete"] and count else None,
        "selected_count": len(selected), "proposals": len(proposals),
        "proposals_per_source_hour": round(len(proposals) * 3_600_000 / duration, 2),
        "proposal_minutes_per_source_hour": round(_covered_ms(proposals) * 60 / duration, 2),
        "correctly_empty_on_negative_source": not selected if gold["complete"] and count == 0 and analysis_complete else None,
        "proposed_footage_ms": sum(b-a for a,b in proposals), "unique_proposed_footage_ms": _covered_ms(proposals),
        "timing": timing, "clip_reviews": assessments, "acceptance_costs": assessment_costs,
        "provider_calls": timing.get("provider_calls", timing.get("local_model_judgments")),
        "processing_seconds": timing.get("total_seconds"), "actual_api_cost": timing.get("remote_api_cost"),
        "technical_measurements": technical,
    }


def markdown_report(result: dict) -> str:
    lines = ["# ChompBench-1 run", "", f"Source: `{result['source_id']}`; job: `{result['job_id']}`.",
             f"Annotation origin: {result['annotation_origin']}; complete: {result['complete_source_annotation']}.",
             "", "| Metric | Value | Numerator | Denominator |", "|---|---:|---:|---:|"]
    for name, metric in result["metrics"].items():
        if isinstance(metric, dict):
            lines.append(f"| {name} | {metric.get('value')} | {metric.get('numerator')} | {metric.get('denominator')} |")
    lines += ["", f"Proposals: {result['proposals']}; selected: {result['selected_count']}; processing seconds: {result['processing_seconds']}; provider calls: {result['provider_calls']}; actual API cost: {result['actual_api_cost']}.",
              "", "Null means unavailable or no valid denominator. Synthetic results verify mechanics only. Human review remains pending unless independent clip reviews are supplied."]
    return "\n".join(lines) + "\n"
