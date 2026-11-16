"""Explicit bridges from existing editorial plans and saved v0.5 edit specs.

No screenshot-to-interval inference. Controller bindings resolve a Director's
region IDs to separately reviewed ledger intervals. Legacy render behavior is
kept as an immutable v0.5 snapshot, not relabeled as a validated new composition.
"""
from __future__ import annotations

from copy import deepcopy

from ..artifacts import artifact_key
from ..director.contract import validate_bundle_shape, validate_plan
from ..subject_lock import TRACK_SCHEMA
from .contract import intersects, validate
from .workflow import base_plan, slot


def from_editorial(editorial: dict, bundle: dict, evidence: dict, bindings: dict,
                   *, width=540, height=960, fps="30") -> dict:
    validate_bundle_shape(bundle)
    validate_plan(editorial, bundle)
    if editorial["decision"] != "select":
        raise ValueError("A reject/abstain Director decision must not create a clip")
    if any(bundle["source"][key] != evidence["source"][key]
           for key in ("sha256", "width", "height", "duration_ms", "audio_stream")):
        raise ValueError("Director and region ledger need matching source metadata and audio track")
    fields = {"schema_version", "editorial_plan_sha256", "evidence_sha256", "regions", "reviewer", "review_reference"}
    if not isinstance(bindings, dict) or set(bindings) != fields or bindings["schema_version"] != "chomp-shot-bindings/1":
        raise ValueError("Supply explicit chomp-shot-bindings/1 controller bindings")
    if bindings["editorial_plan_sha256"] != artifact_key(editorial) or bindings["evidence_sha256"] != artifact_key(evidence):
        raise ValueError("Bindings refer to another storyboard or evidence revision")
    if not all(isinstance(bindings[k], str) and bindings[k].strip() for k in ("reviewer", "review_reference")):
        raise ValueError("Bindings need a reviewer and a reference resolving storyboard uncertainty")
    if not isinstance(bindings["regions"], dict):
        raise ValueError("Region bindings must map Director region IDs to reviewed ledger IDs")
    ledger = {r["id"]: r for r in evidence["regions"]}
    original = {r["id"]: (f, r) for f in bundle["visual_samples"] for r in f["regions"]}
    allowed_roles = {"scene": {"source"}, "camera": {"streamer_container"}, "person": {"person"},
                     "action": {"primary_action", "object"}, "screen_evidence": {"screen_evidence"},
                     "hud": {"protected_hud"}}
    moment = editorial["moment"]
    plan = base_plan(evidence, moment["start_ms"], moment["end_ms"], width=width, height=height, fps=fps)
    plan["provenance"].update(provider=editorial["provenance"]["provider"], model=editorial["provenance"]["model"],
                              editorial_plan_sha256=artifact_key(editorial))
    for number, old in enumerate(editorial["shots"]):
        resolved = []
        for old_id in old["required_region_ids"]:
            new_id = bindings["regions"].get(old_id)
            if not isinstance(new_id, str) or new_id not in ledger:
                raise ValueError(f"Missing reviewed interval binding for {old_id}")
            region = ledger[new_id]
            frame, annotated = original[old_id]
            if (region["role"] not in allowed_roles[annotated["kind"]]
                    or region["role"] != "source" and region["subject_id"] != annotated["subject_id"]):
                raise ValueError(f"Binding changes region role or subject identity for {old_id}")
            sx = evidence["source"]["width"] / frame["width"]
            sy = evidence["source"]["height"] / frame["height"]
            if not intersects(region["box"], tuple(value * scale for value, scale in zip(annotated["box"], (sx, sy, sx, sy)))):
                raise ValueError(f"Reviewed region does not intersect the cited source location for {old_id}")
            if new_id not in [r["id"] for r in resolved]:
                resolved.append(region)
        kind = old["layout"]
        if kind == "letterbox_review":
            resolved = [next((r for r in ledger.values() if r["basis"] == "source_bounds"), None)]
            if resolved[0] is None:
                raise ValueError("Full-source review needs source-bounds evidence")
            layout, slots = "full_source", [slot(resolved[0]["id"], [0, 0, width, height])]
        elif kind in {"split_streamer_main", "evidence_plus_streamer"}:
            role = "primary_action" if kind == "split_streamer_main" else "screen_evidence"
            camera = next((r for r in resolved if r["role"] == "streamer_container"), None)
            main = next((r for r in resolved if r["role"] == role), None)
            if camera is None or main is None:
                raise ValueError("Director split needs reviewed camera and main regions")
            cam_height = round(height * .30 / 2) * 2
            slots = [slot(camera["id"], [0, 0, width, cam_height], name="streamer"),
                     slot(main["id"], [0, cam_height, width, height-cam_height], name="main")]
            layout = "streamer_gameplay_split" if kind == "split_streamer_main" else "streamer_evidence"
        elif kind == "multi_person":
            people = [r for r in resolved if r["role"] in {"person", "streamer_container"}]
            if not 2 <= len(people) <= 4 or len({r["subject_id"] for r in people}) != len(people):
                raise ValueError("Multi-person binding needs 2–4 distinct verified people")
            tile = height // (2 * len(people)) * 2
            slots = [slot(r["id"], [0, i*tile, width, tile if i < len(people)-1 else height-i*tile], name=f"person{i}")
                     for i, r in enumerate(people)]
            layout = "stack_people"
        else:
            roles = {"streamer_only": {"streamer_container"}, "irl_portrait": {"person"},
                     "main_only": {"primary_action", "object", "screen_evidence", "source", "person"}}
            options = [r for r in resolved if r["role"] in roles[kind]]
            if len(options) != 1:
                raise ValueError("Ambiguous main region; request a native shot plan with explicit slots")
            region = options[0]
            layout = {"streamer_container": "streamer_full", "person": "irl_full", "source": "full_source",
                      "primary_action": "gameplay_full", "object": "object_focus", "screen_evidence": "no_webcam"}[region["role"]]
            slots = [slot(region["id"], [0, 0, width, height])]
        # Every cited required region must be displayed somewhere. Extra HUD or
        # subjects never silently disappear during lowering.
        for required in resolved:
            if not any(required["id"] in s["required_region_ids"] for s in slots):
                from .contract import contains
                candidates = [s for s in slots if contains(ledger[s["region_id"]]["box"], required["box"])]
                if len(candidates) != 1:
                    raise ValueError("Required region has no unambiguous destination slot")
                candidates[0]["required_region_ids"].append(required["id"])
        if old["crop_behavior"] in {"pan", "track"}:
            raise ValueError("Moving Director intent needs a native ShotPlan with a reviewed FX01/FX02 crop path")
        plan["shots"].append({"id": f"shot{number+1}", "start_ms": old["start_ms"], "end_ms": old["end_ms"],
                              "layout": layout, "reason": old["reason"], "evidence_refs": [r["id"] for r in resolved],
                              "slots": slots, "transition": {"kind": "cut", "duration_ms": 0}, "graphics": []})
    for name in ("title", "hook"):
        if editorial[name]:
            plan["capability_requests"].append({"operator": f"text.{name}", "owner": "FX04",
                                                "reason": editorial[name]["text"], "evidence_refs": [], "required": False})
    validate(plan, evidence)
    return plan


def subject_summary(edit: dict, source_sha256: str) -> dict:
    """Preserve FX02 distinctions without turning 50ms samples into a guarantee."""
    if edit.get("source_id") != "sha256:" + source_sha256:
        raise ValueError("SubjectLock edit belongs to a different source")
    track = edit.get("subject_tracking")
    if not isinstance(track, dict) or track.get("schema_version") != TRACK_SCHEMA or track.get("source_id") != edit["source_id"]:
        raise ValueError("Expected an embedded, source-matched SubjectLock track")
    start, end = edit["source_start_ms"], edit["source_end_ms"]
    rows = track["observations"]
    spans, last = [], start - 1
    states = {"visible", "redetected", "partially_occluded", "covered_by_source_overlay", "tracker_lost", "not_yet_observed", "unknown"}
    for row in rows:
        at = row["at_ms"]
        if type(at) is not int or not last < at < end or at < start or row["state"] not in states:
            raise ValueError("SubjectLock observations are not ordered inside the saved clip")
        # Sample summaries retain timestamp, box and provenance. No invented
        # interval end, inferred absence, new track or automatic reaction switch.
        spans.append({"at_ms": at, "state": row["state"], "box": row["box"],
                      "confidence": row["confidence"], "reason": row["reason"]})
        last = at
    return {"schema_version": "chomp-shot-tracking-context/1", "source_sha256": source_sha256,
            "edit_sha256": artifact_key(edit), "track_sha256": artifact_key(track),
            "subject_id": track["subject_id"], "tracker_revision": track["tracker_revision"],
            "source_start_ms": start, "source_end_ms": end,
            "observations": spans, "source_spans": deepcopy(track["source_spans"]),
            "protected_regions": deepcopy(track["protected_regions"]),
            "existing_layout_segments": deepcopy(edit["segments"]),
            "review_status": "pending", "interval_visibility_certified": False}


def legacy_envelope(edit: dict) -> dict:
    """Lossless explicit migration envelope: the v0.5 renderer remains authority.

    Blurred mats and the old frame-concat semantics are not approximated by a
    new graph. The next agent can compare a native plan against this exact input.
    """
    from ..clipwork import layout_segments
    if edit.get("schema_version") != "0.5":
        raise ValueError("Legacy migration accepts edit spec v0.5 only")
    layout_segments(edit["segments"], edit["source_start_ms"], edit["source_end_ms"])
    return {"schema_version": "chomp-shot-legacy/1", "legacy_sha256": artifact_key(edit),
            "legacy_edit": deepcopy(edit), "execution": "chomp.manual.rerender-v0.5",
            "native_conversion": "requires_reviewed_regions_and_source_matched_comparison",
            "review_status": "pending", "publish_status": "disabled"}


def attach_tracking(evidence: dict, edit: dict) -> tuple[dict, dict]:
    summary = subject_summary(edit, evidence["source"]["sha256"])
    revised = deepcopy(evidence)
    if any(t["subject_id"] == summary["subject_id"] for t in revised["tracking"]):
        raise ValueError("A track for this subject is already attached; create a reviewed evidence revision")
    revised["tracking"].append({key: summary[key] for key in
                                ("source_sha256", "track_sha256", "subject_id", "tracker_revision", "observations", "source_spans")})
    revised["scene_cuts_ms"] = sorted(set(revised["scene_cuts_ms"]) | {
        s["start_ms"] for s in summary["source_spans"] if s["cut"] and s["start_ms"] > 0})
    return revised, summary
