"""Optional research extension points. No model weights or frameworks imported.

These envelopes extend, rather than mutate, the Director bundle schema. A
future research adapter must explicitly lower evidence into a supported plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Protocol

from .artifacts import artifact_key, artifact_lock, digest_file, source_identity, write_json
from .director.evidence import extract_frame
from .editorial_selection import span, text
from .media import check_audio_track, exec_checked, probe

MODALITIES = {"utterance", "acoustic_event", "chat_reaction", "visual_frame", "motion",
              "subject_track", "visual_region", "scene_transition", "ocr"}


def validate_evidence(row: dict, source_id: str, duration_ms: int) -> dict:
    if not isinstance(row, dict) or row.get("schema_version") != "chomp-research-evidence/1":
        raise ValueError("Expected chomp-research-evidence/1")
    if row.get("source_id") != source_id:
        raise ValueError("Evidence source mismatch")
    text(row.get("id"), "evidence ID")
    span([row.get("start_ms"), row.get("end_ms")], duration_ms)
    if row.get("modality") not in MODALITIES:
        raise ValueError("Unsupported research modality")
    if not isinstance(row.get("provenance"), dict) or not row["provenance"]:
        raise ValueError("Evidence provenance is required")
    if not isinstance(row.get("model_fingerprint"), dict) or not row["model_fingerprint"]:
        raise ValueError("Evidence provider fingerprint is required")
    confidence = row.get("confidence")
    if confidence is not None and (type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError("Invalid evidence confidence")
    if row["modality"] == "acoustic_event":
        if type(row.get("audio_stream")) is not int or row["audio_stream"] < 0:
            raise ValueError("Acoustic events need the selected source audio track")
        labels = row.get("labels")
        if not isinstance(labels, list) or not labels:
            raise ValueError("Acoustic events need simultaneous label/confidence observations")
        seen = set()
        for label in labels:
            if not isinstance(label, dict):
                raise ValueError("Invalid audio label")
            name = text(label.get("label"), "audio label")
            value = label.get("confidence")
            if name in seen or type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Invalid or repeated audio label confidence")
            seen.add(name)
    expected = artifact_key({key: value for key, value in row.items() if key != "id"})
    if row["id"] != "e:" + expected:
        raise ValueError("Evidence stable ID does not match content/provenance")
    return row


def evidence_item(*, source_id: str, duration_ms: int, start_ms: int, end_ms: int, modality: str,
                  model_fingerprint: dict, provenance: dict, confidence=None, **payload) -> dict:
    controlled = {"schema_version", "id", "source_id", "start_ms", "end_ms", "modality",
                  "model_fingerprint", "provenance", "confidence"}
    if controlled.intersection(payload):
        raise ValueError("Evidence payload overrides controlled fields")
    row = {"schema_version": "chomp-research-evidence/1", "source_id": source_id,
           "start_ms": start_ms, "end_ms": end_ms, "modality": modality,
           "model_fingerprint": model_fingerprint, "provenance": provenance, "confidence": confidence, **payload}
    row["id"] = "e:" + artifact_key(row)
    return validate_evidence(row, source_id, duration_ms)


class SemanticAudioProvider(Protocol):
    @property
    def fingerprint(self) -> dict: ...

    def analyze(self, source: Path, *, start_ms: int, end_ms: int, audio_stream: int) -> dict:
        """Return state available/unavailable and source-clock events; never download implicitly."""
        ...


def semantic_audio(source: Path, cache: Path, provider: SemanticAudioProvider | None, *,
                   start_ms: int, end_ms: int, audio_stream: int, rights_ref: str) -> dict:
    text(rights_ref, "rights reference")
    source_id, meta = source_identity(source), probe(source)
    span([start_ms, end_ms], meta["duration_ms"])
    check_audio_track(meta, audio_stream)
    if provider is None:
        return {"state": "unavailable", "reason": "model_not_configured", "events": [], "source_id": source_id,
                "audio_stream": audio_stream, "cache_hit": False}
    fingerprint = provider.fingerprint
    if not isinstance(fingerprint, dict) or not fingerprint:
        raise ValueError("Audio model fingerprint is required")
    request = {"source_id": source_id, "audio_stream": audio_stream, "range_ms": [start_ms, end_ms],
               "provider": fingerprint, "contract": "semantic-audio/1"}
    key = artifact_key(request)
    path = cache / "semantic-audio" / f"{key}.json"
    with artifact_lock(cache / "locks" / f"{key}.lock"):
        cached = path.is_file()
        if cached:
            result = json.loads(path.read_text(encoding="utf-8"))
        else:
            raw = provider.analyze(source, start_ms=start_ms, end_ms=end_ms, audio_stream=audio_stream)
            if not isinstance(raw, dict) or raw.get("state") not in {"available", "unavailable"} or not isinstance(raw.get("events"), list):
                raise ValueError("Invalid semantic audio provider response")
            if raw["state"] == "unavailable" and (raw["events"] or not raw.get("reason")):
                raise ValueError("Unavailable models need a reason and cannot supply events")
            result = {"state": raw["state"], "reason": raw.get("reason"), "request": request, "events": []}
            for event in raw["events"]:
                if not isinstance(event, dict) or set(event) - {"start_ms", "end_ms", "labels", "confidence"}:
                    raise ValueError("Unexpected acoustic event fields")
                result["events"].append(evidence_item(source_id=source_id, duration_ms=meta["duration_ms"],
                    modality="acoustic_event", model_fingerprint=fingerprint, provenance={"request_key": key},
                    audio_stream=audio_stream, **event))
        if result.get("request") != request or result.get("state") not in {"available", "unavailable"}:
            raise ValueError("Invalid semantic audio cache provenance")
        for event in result["events"]:
            validate_evidence(event, source_id, meta["duration_ms"])
            if event["audio_stream"] != audio_stream or not start_ms <= event["start_ms"] < event["end_ms"] <= end_ms:
                raise ValueError("Acoustic event is outside its requested source track/interval")
        if source_identity(source) != source_id:
            raise ValueError("Source changed during semantic audio analysis")
        # Unavailable states are not cached indefinitely; installing a provider
        # with the same fingerprint can make a later explicit retry useful.
        if not cached and result["state"] == "available":
            write_json(path, result)
    return {**result, "cache_hit": cached}


@dataclass(frozen=True)
class RetrievalBudget:
    max_frames: int = 8
    max_interval_ms: int = 120_000

    def validate(self):
        if type(self.max_frames) is not int or not 1 <= self.max_frames <= 64:
            raise ValueError("Frame budget must be 1–64")
        if type(self.max_interval_ms) is not int or not 1 <= self.max_interval_ms <= 600_000:
            raise ValueError("Visual interval budget must be 1–600000 source milliseconds")


def sample_times(start_ms: int, end_ms: int, *, count: int = 5, scene_boundaries: tuple[int, ...] = ()) -> list[int]:
    span([start_ms, end_ms], end_ms)
    if type(count) is not int or not 1 <= count <= 64:
        raise ValueError("Sample count must be 1–64")
    if any(type(t) is not int or not start_ms <= t < end_ms for t in scene_boundaries):
        raise ValueError("Scene boundaries must be source milliseconds inside the interval")
    # Leave a final sampling cell for the decoder's next actual presentation
    # timestamp, rather than requesting the final millisecond of the interval.
    samples = {start_ms + (end_ms - start_ms) * i // count for i in range(count)}
    return sorted(samples | set(scene_boundaries))


def retrieve_frames(source: Path, cache: Path, *, start_ms: int, end_ms: int, times: list[int],
                    budget: RetrievalBudget = RetrievalBudget(), rights_ref: str,
                    protected_regions: list[dict] | None = None) -> dict:
    text(rights_ref, "rights reference")
    budget.validate()
    source_id, meta = source_identity(source), probe(source)
    span([start_ms, end_ms], meta["duration_ms"])
    if end_ms - start_ms > budget.max_interval_ms or len(times) > budget.max_frames:
        raise ValueError("Visual retrieval budget exceeded")
    if len(times) != len(set(times)) or any(type(t) is not int or not start_ms <= t < end_ms for t in times):
        raise ValueError("Requested visual times must be unique source-clock instants")
    regions = protected_regions or []
    for region in regions:
        if not isinstance(region, dict) or not region.get("id") or not isinstance(region.get("box_normalized"), list):
            raise ValueError("Protected regions need stable IDs and normalized source boxes")
        box = region["box_normalized"]
        if len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
            raise ValueError("Invalid protected region geometry")
        x, y, w, h = box
        if not 0 <= x < x + w <= 1 or not 0 <= y < y + h <= 1:
            raise ValueError("Protected region outside the source frame")
    version = exec_checked(["ffmpeg", "-version"], timeout=10).stdout.splitlines()[0]
    frames, hits = [], 0
    for requested in sorted(times):
        key = artifact_key(["research-frame/1", source_id, version, requested])
        root = cache / "visual"
        entry, asset = root / f"{key}.json", root / f"{key}.png"
        with artifact_lock(cache / "locks" / f"{key}.lock"):
            if entry.is_file():
                frame = json.loads(entry.read_text(encoding="utf-8"))
                if frame.get("source_id") != source_id or frame.get("requested_ms") != requested or frame.get("sha256") != digest_file(asset):
                    raise ValueError("Visual cache provenance/hash mismatch")
                hits += 1
            else:
                pixels, actual, width, height = extract_frame(source, requested)
                root.mkdir(parents=True, exist_ok=True)
                asset.write_bytes(pixels)
                frame = {"id": "f:" + key, "source_id": source_id, "requested_ms": requested, "at_ms": actual,
                         "path": str(asset.resolve()), "sha256": sha256(pixels).hexdigest(),
                         "width": width, "height": height, "provenance": {"decoder": version}}
                write_json(entry, frame)
            if type(frame["at_ms"]) is not int or not requested <= frame["at_ms"] < end_ms:
                raise ValueError("Decoded presentation timestamp is outside the visual request")
        frames.append({**frame, "protected_regions": regions})
    if source_identity(source) != source_id:
        raise ValueError("Source changed during frame retrieval")
    return {"schema_version": "chomp-visual-retrieval/1", "source_id": source_id,
            "range_ms": [start_ms, end_ms], "frames": frames, "cache_hits": hits,
            "budget": {"max_frames": budget.max_frames, "max_interval_ms": budget.max_interval_ms}}


def collect_candidates(lanes: dict[str, list[dict]], evidence: list[dict], *, source_id: str,
                       duration_ms: int, max_candidates: int = 60,
                       experimental_lane_order: bool = False) -> dict:
    """Research-only collection, preserving overlap and uncalibrated confidence.

    Without explicit experimental opt-in, overflow fails instead of silently
    applying dictionary order as a production allocation/fusion policy.
    """
    if type(max_candidates) is not int or not 0 <= max_candidates <= 10_000:
        raise ValueError("Invalid candidate budget")
    registry = {}
    for item in evidence:
        validate_evidence(item, source_id, duration_ms)
        if item["id"] in registry:
            raise ValueError("Duplicate evidence ID")
        registry[item["id"]] = item
    candidates, seen = [], set()
    for lane, proposals in lanes.items():
        text(lane, "candidate lane")
        for row in proposals:
            if not isinstance(row, dict):
                raise ValueError("Candidate proposal must be an object")
            cid = text(row.get("candidate_id"), "candidate ID")
            if cid in seen:
                raise ValueError("Candidate IDs must be unique across lanes")
            seen.add(cid)
            interval = span(row.get("range_ms"), duration_ms)
            refs = row.get("evidence_ids")
            if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or x not in registry for x in refs):
                raise ValueError("Candidate needs known source evidence")
            if len(refs) != len(set(refs)):
                raise ValueError("Candidate repeats evidence IDs")
            if not any(registry[eid]["start_ms"] < interval[1] and registry[eid]["end_ms"] > interval[0] for eid in refs):
                raise ValueError("Candidate interval does not overlap its evidence")
            candidates.append({**row, "source_id": source_id, "lanes": [lane]})
    if len(candidates) > max_candidates and experimental_lane_order is not True:
        raise ValueError("Candidate budget needs an explicit research allocation policy; lane-order truncation is experimental")
    retained = candidates[:max_candidates]
    input_counts = {lane: len(rows) for lane, rows in lanes.items()}
    retained_counts = {lane: sum(lane in row['lanes'] for row in retained) for lane in lanes}
    return {"schema_version": "chomp-research-candidates/1", "source_id": source_id, "duration_ms": duration_ms,
            "candidates": retained,
            "omitted_count": max(0, len(candidates) - max_candidates), "policy": "experimental_lane_order" if len(candidates) > max_candidates else "collection_only_no_fusion",
            "experimental": len(candidates) > max_candidates, "production_fusion_enabled": False,
            "allocation_diagnostics": {"input_by_lane": input_counts, "retained_by_lane": retained_counts,
                "omitted_by_lane": {lane: input_counts[lane] - retained_counts[lane] for lane in lanes},
                "completely_excluded_lanes": [lane for lane in lanes if input_counts[lane] and not retained_counts[lane]],
                "empty_input_lanes": [lane for lane in lanes if not input_counts[lane]],
                "lane_order": list(lanes), "policy_owner": "DEEPSCOUT; KR-01 through KR-08 research coordination"},
            "confidence_notice": "Provider confidence values are not cross-model calibrated probabilities."}
