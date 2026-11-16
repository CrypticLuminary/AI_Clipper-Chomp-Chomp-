"""Strict plan validation and conservative, source-clock spatial feasibility.

The evidence ledger is supplied by the controller, never by a Director response.
Human interval entries are explicit attestations, not authenticated identities.
Sparse detector samples deliberately cannot certify an entire moving shot.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math

from ..artifacts import artifact_key
from ..director.contract import check_schema
from ..media import validate_roi
from . import VERSION
from .capabilities import CAPABILITY_REQUEST_OWNERS


def fraction(value: dict) -> Fraction:
    return Fraction(value["num"], value["den"])


def frame_index(ms: int, start_ms: int, rate: Fraction) -> int:
    """First output frame at or after the requested source time (never early)."""
    return math.ceil(Fraction(ms - start_ms, 1000) * rate)


def intersects(a, b) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def contains(outer, inner, margin: float = 0) -> bool:
    return (outer[0] + margin <= inner[0] and outer[1] + margin <= inner[1]
            and inner[0] + inner[2] <= outer[0] + outer[2] - margin
            and inner[1] + inner[3] <= outer[1] + outer[3] - margin)


def geometry(slot: dict, region: dict, sar: Fraction, *, xy=None, zoom: float = 1) -> tuple:
    """Return source viewport and its affine projection to canvas coordinates.

    Crop is source-pixel based. SAR correction happens before fit and zoom.
    Endpoint checks bound the entire linear crop path and monotone zoom curves.
    Pixel rounding still needs rendered-frame acceptance in FX03-F.
    """
    x, y, rw, rh = region["box"]
    if xy is not None:
        x, y = xy
    dx, dy, dw, dh = slot["rect"]
    fit = max if slot["fit"] == "cover" else min
    scale = fit(dw / (rw * float(sar)), dh / rh) * zoom
    sx, sy = scale * float(sar), scale
    ox, oy = dx + (dw - rw * sx) / 2, dy + (dh - rh * sy) / 2
    left, top = max(x, x + (dx - ox) / sx), max(y, y + (dy - oy) / sy)
    right, bottom = min(x + rw, x + (dx + dw - ox) / sx), min(y + rh, y + (dy + dh - oy) / sy)
    return (left, top, right - left, bottom - top), (sx, sy, ox - x * sx, oy - y * sy)


def project(box, transform) -> tuple:
    sx, sy, ox, oy = transform
    x, y, w, h = box
    return x * sx + ox, y * sy + oy, w * sx, h * sy


def pointer_blocks(slot: dict, graphic: dict, shot: dict, regions: dict, sar: Fraction,
                   exclusions: list) -> tuple:
    """Place a small arrow beside the target if there is room clear of other content."""
    _, transform = geometry(slot, regions[slot["region_id"]], sar)
    target = project(regions[graphic["region_id"]]["box"], transform)
    tile = slot["rect"]
    head = min(18, max(4, tile[2] // 20))
    shaft = 2 * head
    thickness = min(graphic["thickness"], head)
    center = round(target[1] + target[3] / 2)
    later = shot["slots"][shot["slots"].index(slot) + 1:]
    obstacles = [*exclusions, *(s["rect"] for s in later)]
    obstacles.extend(project(regions[ref]["box"], transform) for ref in slot["required_region_ids"]
                     if ref in regions and ref != graphic["region_id"])
    options = ((math.floor(target[0]) - 5 - shaft - head, False),
               (math.ceil(target[0] + target[2]) + 5, True))
    for start, flip in options:
        bounds = (start, center - head, shaft + head, 2 * head + 1)
        if not contains(tile, bounds, margin=2) or any(intersects(bounds, r) for r in obstacles):
            continue
        parts = [(start, center - thickness // 2, shaft, thickness)]
        for x in range(0, head, math.ceil(head / 4)):
            half_height = max(1, head - x)
            parts.append((start + shaft + x, center - half_height,
                          min(math.ceil(head / 4), head - x), 2 * half_height + 1))
        if flip:
            parts = [(2 * start + shaft + head - x - w, y, w, h) for x, y, w, h in parts]
        return tuple(parts)
    return ()


@dataclass(frozen=True)
class Feasibility:
    plan_sha256: str
    evidence_sha256: str
    problems: tuple[dict, ...]
    timing: tuple[dict, ...]
    cost: dict

    @property
    def renderable(self) -> bool:
        return not any(p["severity"] in {"blocked", "needs_review"} for p in self.problems)

    def as_dict(self) -> dict:
        status = "renderable" if self.renderable else (
            "blocked" if any(p["severity"] == "blocked" for p in self.problems) else "needs_review")
        return {"status": status, "renderer_version": VERSION, "plan_sha256": self.plan_sha256,
                "evidence_sha256": self.evidence_sha256, "problems": list(self.problems),
                "requested_vs_realized_timing": list(self.timing), "resource_estimate": self.cost,
                "review_status": "pending", "publish_status": "disabled"}


def validate(plan: dict, evidence: dict) -> Feasibility:
    check_schema(plan, "shot_plan")
    check_schema(evidence, "shot_evidence")
    # jsonschema accepts 1.0 as an integer. The compiler requires real JSON ints.
    def strict_numbers(node, key=None):
        if isinstance(node, dict):
            for name, value in node.items():
                strict_numbers(value, name)
        elif isinstance(node, list):
            for value in node:
                strict_numbers(value, key)
        elif isinstance(node, float):
            if not math.isfinite(node) or key not in {"scale", "opacity", "confidence", "usable_fraction"}:
                raise ValueError("Times, geometry and rational components require JSON integers")
    strict_numbers(plan)
    strict_numbers(evidence)
    source = evidence["source"]
    if plan["source_sha256"] != source["sha256"] or plan["evidence_sha256"] != artifact_key(evidence):
        raise ValueError("ShotPlan cites a different source or evidence revision")
    start, end = plan["start_ms"], plan["end_ms"]
    if not 0 <= start < end <= source["duration_ms"] or end - start > 60_000:
        raise ValueError("ShotPlan must select one continuous range of at most 60 seconds")
    canvas = plan["canvas"]
    cw, ch, rate = canvas["width"], canvas["height"], fraction(canvas["fps"])
    if any(type(v) is not int or v % 2 for v in (cw, ch)) or not 1 <= rate <= 60:
        raise ValueError("Canvas requires even integer dimensions and 1–60 output fps")
    if cw * ch > 1080 * 1920:
        raise ValueError("Canvas exceeds the FX03 pixel budget")
    sar = fraction(source["sar"])
    if not Fraction(1, 8) <= sar <= 8:
        raise ValueError("Unsupported source pixel aspect ratio")
    index = {}
    for row in [*evidence["regions"], *evidence["facts"]]:
        if row["id"] in index or not 0 <= row["start_ms"] < row["end_ms"] <= source["duration_ms"]:
            raise ValueError("Evidence IDs must be unique with valid source intervals")
        index[row["id"]] = row
    regions = {r["id"]: r for r in evidence["regions"]}
    for row in regions.values():
        validate_roi(row["box"], source["width"], source["height"], row["id"])
        if row["basis"] == "source_bounds" and (row["role"] != "source" or row["box"] != [0, 0, source["width"], source["height"]]):
            raise ValueError("Source-bounds evidence cannot identify a person or a cropped subject")
        if row["basis"] in {"human_interval", "decoded_interval"} and not (row["reviewer"] and row["reviewer"].strip()):
            raise ValueError("Reviewed interval evidence requires a named verifier and reference")
    cuts = evidence["scene_cuts_ms"]
    if cuts != sorted(set(cuts)) or any(not 0 < t < source["duration_ms"] for t in cuts):
        raise ValueError("Scene cuts must be sorted unique source timestamps")
    for track in evidence["tracking"]:
        if track["source_sha256"] != source["sha256"]:
            raise ValueError("Tracking evidence cites a different source")
        previous = -1
        for row in track["observations"]:
            if not previous < row["at_ms"] < source["duration_ms"]:
                raise ValueError("Track observations must be ordered source timestamps")
            previous = row["at_ms"]
            if row["box"] is not None:
                validate_roi(row["box"], source["width"], source["height"], "Track observation")
        previous = None
        for span in track["source_spans"]:
            if not 0 <= span["start_ms"] < span["end_ms"] <= source["duration_ms"]:
                raise ValueError("Invalid tracking source span")
            if previous is not None and span["start_ms"] != previous:
                raise ValueError("Tracking source spans must be contiguous")
            previous = span["end_ms"]
            validate_roi(span["overlay"], source["width"], source["height"], "Source overlay")
            if span["cut"] and span["start_ms"] > 0 and span["start_ms"] not in cuts:
                raise ValueError("A tracking source cut is missing from the evidence cut index")
    for box in plan["caption_exclusions"]:
        validate_roi(box, cw, ch, "Caption exclusion")
    problems, timing, problem_keys = [], [], set()

    def problem(code, shot, detail, severity="blocked"):
        key = (code, shot, detail, severity)
        if key not in problem_keys:
            problem_keys.add(key)
            problems.append({"code": code, "shot_id": shot, "detail": detail, "severity": severity})

    def citations(refs, a, b, shot):
        if len(set(refs)) != len(refs):
            raise ValueError("Repeated evidence citation")
        for ref in refs:
            row = index.get(ref)
            if row is None or not row["start_ms"] < b or not a < row["end_ms"]:
                problem("invalid_citation", shot, ref)
            elif row["support"] != "observed":
                problem("uncertain_editorial_basis", shot, ref, "needs_review")

    def region_for(ref, a, b, shot):
        row = regions.get(ref)
        if row is None:
            problem("missing_region", shot, ref)
            return None
        if not row["start_ms"] <= a < b <= row["end_ms"]:
            problem("region_outside_verified_interval", shot, ref, "needs_review")
        if row["support"] != "observed" or row["basis"] in {"sample", "fx02_samples"}:
            problem("interval_visibility_unverified", shot, ref, "needs_review")
        if row["visibility"] not in {"visible", "redetected"}:
            problem(row["visibility"], shot, ref, "needs_review")
        if row["role"] != "source" and any(a < t < b for t in cuts):
            problem("region_crosses_scene_cut", shot, ref, "needs_review")
        for track in evidence["tracking"]:
            if row["subject_id"] != track["subject_id"] or row["role"] == "source":
                continue
            for span in track["source_spans"]:
                if span["start_ms"] < b and a < span["end_ms"] and span["overlay"] is not None and intersects(row["box"], span["overlay"]):
                    problem("protected_region_covered_by_source_overlay", shot, ref)
                    break
            lost = {o["state"] for o in track["observations"] if a <= o["at_ms"] < b
                    and o["state"] not in {"visible", "redetected"}}
            if lost:
                reviewed = row["basis"] in {"human_interval", "decoded_interval"}
                problem("reviewed_tracking_gap" if reviewed else "tracking_gap", shot,
                        f"{ref}: {', '.join(sorted(lost))}; detector loss is not absence",
                        "warning" if reviewed else "needs_review")
        return row

    seen, cursor = set(), start
    for number, shot in enumerate(plan["shots"]):
        sid, a, b = shot["id"], shot["start_ms"], shot["end_ms"]
        if sid in seen or a != cursor or not a < b <= end:
            raise ValueError("Unique ordered shots must exactly cover the selected interval")
        seen.add(sid)
        cursor = b
        fa, fb = frame_index(a, start, rate), frame_index(b, start, rate)
        if fb <= fa:
            problem("subframe_shot", sid, "Shot collapses on the output frame grid")
        timing.append({"shot_id": sid, "requested_start_ms": a, "requested_end_ms": b,
                       "start_frame": fa, "end_frame_exclusive": fb,
                       "realized_start_ms": float(Fraction(fa * 1000, 1) / rate + start),
                       "realized_end_ms": float(Fraction(fb * 1000, 1) / rate + start)})
        citations(shot["evidence_refs"], a, b, sid)
        transition = shot["transition"]
        if transition["kind"] == "cut" and transition["duration_ms"] != 0:
            raise ValueError("A hard cut must have zero transition duration")
        if transition["kind"] == "dissolve":
            if number == 0 or not 50 <= transition["duration_ms"] <= min(400, b - a):
                problem("invalid_transition", sid, "Dissolve needs a previous shot and 50–400ms inside incoming shot")
            if any(a <= t < a + transition["duration_ms"] for t in cuts):
                problem("dissolve_crosses_source_cut", sid, "Choose an explicit hard cut")
            actual_frames = frame_index(a + transition["duration_ms"], start, rate) - fa
            if actual_frames < 1 or Fraction(actual_frames, 1) / rate > Fraction(2, 5):
                problem("transition_grid_limit", sid, "Dissolve must occupy 1+ frames and at most 400ms on the output grid")
        # Outgoing view remains visible during the incoming dissolve. Both views
        # must have evidence for that same advancing source-time interval.
        extra = (plan["shots"][number + 1]["transition"]["duration_ms"]
                 if number + 1 < len(plan["shots"]) else 0)
        last_exclusive = frame_index(b + extra, start, rate)
        last_source_ms = Fraction((last_exclusive - 1) * 1000, 1) / rate + start
        evidence_end = min(end, max(b + extra, math.floor(last_source_ms) + 1))
        if b - a < 600:
            problem("brief_shot", sid, "Check whether this short visual switch is intentional", "warning")
        slots, roles, people = {}, set(), set()
        for slot in shot["slots"]:
            if slot["id"] in slots:
                raise ValueError("Shot slot IDs must be unique")
            slots[slot["id"]] = slot
            validate_roi(slot["rect"], cw, ch, "Destination slot")
            if any(type(v) is not int or v % 2 for v in slot["rect"]):
                raise ValueError("Destination slot coordinates and dimensions must be even integers")
            region = region_for(slot["region_id"], a, evidence_end, sid)
            if region is None:
                continue
            roles.add(region["role"])
            if region["role"] in {"person", "streamer_container"}:
                people.add(region["subject_id"])
            path = slot["crop_path"]
            if path and (path[0][0] != a or path[-1][0] != b or any(p[0] >= q[0] for p, q in zip(path, path[1:]))):
                raise ValueError("Crop path must be ordered and cover its shot endpoints")
            positions = [p[1:] for p in path] or [region["box"][:2]]
            for xy in positions:
                validate_roi([*xy, *region["box"][2:]], source["width"], source["height"], "Crop path")
            for left, right in zip(path, path[1:]):
                speed = math.hypot((right[1] - left[1]) / region["box"][2],
                                   (right[2] - left[2]) / region["box"][3]) * 1000 / (right[0] - left[0])
                if speed > 1.2:
                    problem("pan_speed_needs_review", sid, "Crop exceeds 1.2 viewport diagonals per second; revise using FX01 smoothing", "needs_review")
                    break
            zooms = slot["zoom"]
            if zooms and (zooms[0]["at_ms"] != a or zooms[-1]["at_ms"] != b
                          or any(p["at_ms"] >= q["at_ms"] for p, q in zip(zooms, zooms[1:]))):
                raise ValueError("Zoom keys must be ordered and cover shot endpoints")
            maximum = max((z["scale"] for z in zooms), default=1)
            if any(abs(q["scale"]-p["scale"])*1000/(q["at_ms"]-p["at_ms"]) > 1.5
                   for p, q in zip(zooms, zooms[1:])):
                problem("zoom_speed_needs_review", sid, "Scale rate exceeds the experimental motion budget", "needs_review")
            if maximum > 1 and slot["fit"] != "cover":
                problem("zoom_requires_cover", sid, "Zooming a padded view is not supported")
            required = [region_for(ref, a, evidence_end, sid) for ref in slot["required_region_ids"]]
            swept = {target["id"]: [] for target in required if target is not None}
            if not required:
                problem("missing_protected_subject", sid, "Each slot needs at least one explicit required region")
            for xy in positions:
                for zoom in {1, maximum}:
                    viewport, transform = geometry(slot, region, sar, xy=xy, zoom=zoom)
                    if max(transform[:2]) > 4:
                        problem("excessive_upscale", sid, "Source pixels would be enlarged more than 4x", "needs_review")
                    for target in required:
                        if target is None:
                            continue
                        box = target["box"]
                        projected = project(box, transform)
                        swept[target["id"]].append(projected)
                        if not contains(viewport, box):
                            problem("protected_region_cropped", sid, target["id"])
                        if min(projected[2:]) < target["minimum_output_px"]:
                            problem("region_unreadable", sid, target["id"])
                        if any(intersects(projected, exclusion) for exclusion in plan["caption_exclusions"]):
                            problem("caption_exclusion_conflict", sid, target["id"])
                        later = shot["slots"][list(slots).index(slot["id"]) + 1:]
                        if any(intersects(projected, overlay["rect"]) for overlay in later):
                            problem("slot_hides_required_region", sid, target["id"])
            # A moving subject can cross a caption/inset between safe endpoints.
            # Bound the whole path with a conservative projected union envelope.
            for target_id, boxes in swept.items():
                left, top = min(r[0] for r in boxes), min(r[1] for r in boxes)
                envelope = (left, top, max(r[0]+r[2] for r in boxes)-left, max(r[1]+r[3] for r in boxes)-top)
                if any(intersects(envelope, exclusion) for exclusion in plan["caption_exclusions"]):
                    problem("caption_crosses_subject_motion", sid, target_id)
                later = shot["slots"][list(slots).index(slot["id"]) + 1:]
                if any(intersects(envelope, overlay["rect"]) for overlay in later):
                    problem("overlay_crosses_subject_motion", sid, target_id)
            if shot["layout"] == "full_source" and (region["basis"] != "source_bounds" or slot["fit"] != "contain" or path or zooms):
                problem("full_source_must_preserve_frame", sid, "Full-source layout must contain the complete source with no crop motion or zoom")
        needed = {"streamer_gameplay_split": {"streamer_container", "primary_action"},
                  "streamer_evidence": {"streamer_container", "screen_evidence"},
                  "streamer_full": {"streamer_container"}, "gameplay_full": {"primary_action"},
                  "object_focus": {"object"}, "irl_full": {"person"}, "full_source": {"source"}}
        if not needed.get(shot["layout"], set()) <= roles:
            problem("layout_role_mismatch", sid, shot["layout"])
        if shot["layout"] in {"dual_people", "stack_people"} and len(people) < 2:
            problem("two_people_not_verified", sid, "Two distinct, interval-verified people are required")
        if shot["layout"] == "dual_people" and (len(slots) != 2 or len(people) != 2):
            problem("layout_slot_count", sid, "Dual-people composition requires exactly two distinct people and slots")
        if shot["layout"] in {"streamer_full", "gameplay_full", "object_focus", "full_source", "irl_full", "no_webcam"} and len(slots) != 1:
            problem("layout_slot_count", sid, "This layout requires one slot")
        if shot["layout"] in {"streamer_gameplay_split", "streamer_evidence", "picture_in_picture"} and len(slots) != 2:
            problem("layout_slot_count", sid, "This layout requires two slots")
        for graphic in shot["graphics"]:
            ga, gb = graphic["start_ms"], graphic["end_ms"]
            if not a <= ga < gb <= b:
                raise ValueError("Graphics must be inside their shot")
            if frame_index(gb, start, rate) <= frame_index(ga, start, rate):
                problem("subframe_graphic", sid, "The graphic has no visible frame on the output grid")
            citations(graphic["evidence_refs"], ga, gb, sid)
            slot = slots.get(graphic["slot_id"])
            target = region_for(graphic["region_id"], ga, gb, sid)
            if slot is None or target is None:
                problem("graphic_missing_target", sid, graphic["region_id"])
                continue
            if slot["crop_path"] or slot["zoom"]:
                problem("moving_graphic_unavailable", sid, "Graphics currently require a held source view")
            if graphic["region_id"] not in slot["required_region_ids"]:
                problem("graphic_target_not_protected", sid, graphic["region_id"])
            if graphic["kind"] == "spotlight" and len(slot["required_region_ids"]) > 1:
                problem("spotlight_hides_context", sid, "Use outline when multiple regions must remain readable")
            if graphic["kind"] == "spotlight" and any(intersects(slot["rect"], r) for r in plan["caption_exclusions"]):
                problem("spotlight_caption_conflict", sid, "Dimming overlaps a reserved caption area")
            later = shot["slots"][list(slots).index(slot["id"]) + 1:]
            if graphic["kind"] == "spotlight" and any(intersects(slot["rect"], s["rect"]) for s in later):
                problem("spotlight_covers_another_slot", sid, "Dimming would cover an inset composed above this slot")
            if graphic["kind"] == "pointer" and slot["region_id"] in regions and not pointer_blocks(
                    slot, graphic, shot, regions, sar, plan["caption_exclusions"]):
                problem("pointer_has_no_clear_placement", sid, "Choose a shot with room beside the target or request an outline")
    if cursor != end:
        raise ValueError("Shots must end at the selected source end")
    for request in plan["capability_requests"]:
        citations(request["evidence_refs"], start, end, None)
        owner = CAPABILITY_REQUEST_OWNERS.get(request["operator"])
        if owner is None or request["owner"] != owner:
            problem("invalid_capability_request", None, f"Check capabilities for {request['operator']} and its owner")
            continue
        problem("delegated_capability", None, f"{request['operator']} -> {request['owner']}: {request['reason']}",
                "blocked" if request["required"] else "delegated")
    cost = {"decode_inputs": 1, "shot_views": len(plan["shots"]),
            "max_slots_per_view": max(len(s["slots"]) for s in plan["shots"]),
            "estimated_class": "high" if len(plan["shots"]) > 4 else "moderate",
            "benchmark_status": "not_measured"}
    return Feasibility(artifact_key(plan), artifact_key(evidence), tuple(problems), tuple(timing), cost)
