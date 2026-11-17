"""Provider-neutral schema and source-clock validation for editorial intent."""
from __future__ import annotations

from functools import cache
import json
from pathlib import Path

from ..artifacts import artifact_key
from ..models import checked_span

CONTRACT_DIR = Path(__file__).with_name("contracts")
CONTROLLER_FIELDS = ("review_status", "publish_status", "provenance")


@cache
def schema(name: str) -> dict:
    return read_json(CONTRACT_DIR / f"{name}.schema.json")


def read_json(path: Path):
    def unique_fields(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"Duplicate JSON key: {key}")
            out[key] = value
        return out

    def invalid_number(value):
        raise ValueError(f"Invalid JSON number: {value}")

    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_fields,
                      parse_constant=invalid_number)


def check_schema(value: dict, name: str, *, contract: dict | None = None) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise RuntimeError("Install chomp-chomp[director] for schema validation") from exc
    error = next(Draft202012Validator(schema(name) if contract is None else contract).iter_errors(value), None)
    if error:
        location = ".".join(str(i) for i in error.absolute_path) or name
        raise ValueError(f"Invalid {location}: {error.message}")


def bundle_digest(bundle: dict) -> str:
    return artifact_key({key: value for key, value in bundle.items() if key != "evidence_hash"})


def _inside(row: dict, bounds: tuple[int, int], description: str) -> None:
    start, end = row["start_ms"], row["end_ms"]
    checked_span(start, end, bounds[1])
    if start < bounds[0]:
        raise ValueError(f"{description} starts before its context")


def _instant(value: int, bounds: tuple[int, int], description: str) -> None:
    if type(value) is not int or not bounds[0] <= value < bounds[1]:
        raise ValueError(f"{description} is outside its source-clock interval")


def validate_bundle_shape(bundle: dict) -> None:
    check_schema(bundle, "evidence_bundle")
    if bundle_digest(bundle) != bundle["evidence_hash"]:
        raise ValueError("Evidence bundle hash does not match")
    duration = bundle["source"]["duration_ms"]
    context = bundle["context"]
    _inside(context, (0, duration), "Evidence context")
    bounds = context["start_ms"], context["end_ms"]
    if bounds[1] - bounds[0] > 120_000:
        raise ValueError("Evidence context exceeds 120 seconds")
    candidate = bundle["candidate"]
    _inside(candidate, bounds, "Candidate")
    _instant(candidate["anchor_ms"], (candidate["start_ms"], candidate["end_ms"]), "Candidate anchor")

    ids = {candidate["id"]}

    def register(item):
        if item["id"] in ids:
            raise ValueError(f"Duplicate evidence or region ID: {item['id']}")
        ids.add(item["id"])

    modality_flags = bundle["modality_flags"]
    for field, mode in (("utterances", "transcript"), ("audio_events", "audio"), ("chat_events", "chat")):
        rows = bundle[field]
        if rows and not modality_flags[mode]:
            raise ValueError(f"{field} supplied for an unavailable modality")
        for row in rows:
            register(row)
            _inside(row, bounds, field)
            for word in row.get("words", []):
                register(word)
                _inside(word, (row["start_ms"], row["end_ms"]), "Caption word")
            if field == "utterances" and row["words"]:
                joined = "".join(word["text"] for word in row["words"])
                if "".join(joined.split()) != "".join(row["text"].split()):
                    raise ValueError("Word timing evidence does not preserve the utterance")

    frames = bundle["visual_samples"]
    if modality_flags["visual"] != bool(frames):
        raise ValueError("Visual modality flag disagrees with the sampled frames")
    seen_paths = set()
    for frame in frames:
        register(frame)
        if frame["path"] in seen_paths:
            raise ValueError("A frame path can only identify one sample")
        seen_paths.add(frame["path"])
        _instant(frame["requested_ms"], bounds, "Requested frame")
        _instant(frame["at_ms"], (frame["requested_ms"], bounds[1]), "Decoded frame")
        for region in frame["regions"]:
            register(region)
            x, y, width, height = region["box"]
            if (any(type(value) is not int for value in region["box"])
                    or min(width, height) < 1 or x + width > frame["width"] or y + height > frame["height"]):
                raise ValueError(f"Region {region['id']} is outside its evidence image")
            if region["origin"] == "source_bounds" and (
                region["kind"] != "scene" or region["box"] != [0, 0, frame["width"], frame["height"]]
            ):
                raise ValueError("A source-bounds region must cover its full image")


def _index(bundle: dict, mode: str) -> dict[str, tuple[str, int, int]]:
    index = {}
    for field, modality in (("utterances", "transcript"), ("audio_events", "audio"), ("chat_events", "chat")):
        for row in bundle[field]:
            index[row["id"]] = modality, row["start_ms"], row["end_ms"]
    if mode == "frames":
        for frame in bundle["visual_samples"]:
            index[frame["id"]] = "visual", frame["at_ms"], frame["at_ms"] + 1
    return index


def validate_plan(plan: dict, bundle: dict, *, expected_mode: str | None = None) -> None:
    """Validate the contract and cited evidence. Playback review verifies meaning."""
    check_schema(plan, "editorial_plan")
    provenance = plan["provenance"]
    mode = provenance["mode"]
    if expected_mode is not None and mode != expected_mode:
        raise ValueError("The plan's evidence mode differs from the requested mode")
    if (provenance["bundle_sha256"] != bundle["evidence_hash"]
            or provenance["source_sha256"] != bundle["source"]["sha256"]):
        raise ValueError("Plan cites a different source or evidence bundle")
    if mode == "frames" and not bundle["modality_flags"]["visual"]:
        raise ValueError("Frame mode requires verified source images")
    index = _index(bundle, mode)

    def citations(ids, span=None):
        if len(ids) != len(set(ids)) or any(i not in index for i in ids):
            raise ValueError("Citations must be distinct IDs visible in this mode")
        rows = [index[i] for i in ids]
        if span and any(not (start < span[1] and end > span[0]) for _, start, end in rows):
            raise ValueError("A citation is outside the selected cut")
        return rows

    moment = plan["moment"]
    selected = plan["decision"] == "select"
    cut = None
    if selected:
        if moment is None or not plan["shots"] or not plan["beats"]:
            raise ValueError("A selected moment needs story beats and a shot plan")
        _inside(moment, (bundle["context"]["start_ms"], bundle["context"]["end_ms"]), "Selected moment")
        cut = moment["start_ms"], moment["end_ms"]
        if cut[1] - cut[0] > 60_000:
            raise ValueError("A single selected moment cannot exceed 60 seconds")
        _instant(bundle["candidate"]["anchor_ms"], cut, "Original candidate anchor")
        _instant(moment["anchor_ms"], cut, "Editorial anchor")
        citations(moment["evidence_ids"], cut)
    elif moment is not None or any(plan[key] for key in ("shots", "beats", "title", "hook")):
        raise ValueError("Reject and abstain decisions cannot contain a clip or copy")

    for claim in plan["claims"]:
        rows = citations(claim["evidence_ids"], cut)
        if claim["modality"] not in {modality for modality, _, _ in rows}:
            raise ValueError(f"No matching evidence for a {claim['modality']} claim")
    if not selected:
        if plan["captions"]["emphasis_word_ids"]:
            raise ValueError("Only a selected clip can carry caption emphasis")
        return

    beat_times = []
    for beat in plan["beats"]:
        _inside(beat, cut, "Story beat")
        citations(beat["evidence_ids"], (beat["start_ms"], beat["end_ms"]))
        beat_times.append(beat["start_ms"])
    if beat_times != sorted(beat_times) or not {"event", "payoff"}.intersection(beat["role"] for beat in plan["beats"]):
        raise ValueError("Story needs an event or payoff and chronological beats")

    cursor = cut[0]
    for shot in plan["shots"]:
        _inside(shot, cut, "Shot")
        if cursor != shot["start_ms"]:
            raise ValueError("Shots must cover the selected moment without gaps or overlap")
        cursor = shot["end_ms"]
        citations(shot["evidence_ids"], (shot["start_ms"], shot["end_ms"]))
        _validate_shot(shot, bundle, mode)
    if cursor != cut[1]:
        raise ValueError("Shots must finish at the selected end")
    if (mode == "text" or any(shot["uncertain"] for shot in plan["shots"])) and not plan["requires_visual_verification"]:
        raise ValueError("Text-only or uncertain plans require visual verification")

    for field in ("title", "hook"):
        if plan[field] is not None:
            citations(plan[field]["evidence_ids"], cut)
    words = {word["id"] for row in bundle["utterances"] for word in row["words"]
             if word["confidence"] is not None and word["confidence"] >= .5
             and cut[0] <= word["start_ms"] < word["end_ms"] <= cut[1]}
    if not set(plan["captions"]["emphasis_word_ids"]) <= words:
        raise ValueError("Caption emphasis requires confident source word timing")


def _validate_shot(shot: dict, bundle: dict, mode: str) -> None:
    layout, behavior = shot["layout"], shot["crop_behavior"]
    regions, subjects = shot["required_region_ids"], shot["target_subject_ids"]
    if layout == "letterbox_review":
        if regions or subjects or behavior != "full_frame" or not shot["uncertain"]:
            raise ValueError("A review layout must preserve the full frame and flag uncertainty")
        return
    if mode == "text":
        raise ValueError("Text-only plans can only suggest the full-frame review layout")
    eligible = {
        region["id"]: region
        for frame in bundle["visual_samples"]
        if frame["id"] in shot["evidence_ids"] and shot["start_ms"] <= frame["at_ms"] < shot["end_ms"]
        for region in frame["regions"] if region["verified"] or shot["uncertain"]
    }
    if not regions or any(i not in eligible for i in regions):
        raise ValueError("Shot needs regions from cited, in-range frames")
    if len(regions) != len(set(regions)) or len(subjects) != len(set(subjects)):
        raise ValueError("Shot region and subject references must be distinct")
    if not subjects or not set(subjects) <= {eligible[i]["subject_id"] for i in regions}:
        raise ValueError("Shot subjects must be present in the required regions")

    kinds = {eligible[i]["kind"] for i in regions}
    required = {
        "split_streamer_main": {"camera", "action"},
        "streamer_only": {"camera"},
        "evidence_plus_streamer": {"camera", "screen_evidence"},
        "irl_portrait": {"person"},
    }
    if not required.get(layout, set()) <= kinds:
        raise ValueError(f"Missing a required source region for {layout}")
    if layout == "main_only" and not kinds.intersection({"action", "scene", "person", "screen_evidence"}):
        raise ValueError("Main-only framing needs a visible primary subject")
    if layout == "multi_person":
        people = {eligible[i]["subject_id"] for i in regions if eligible[i]["kind"] in {"person", "camera"}}
        if len(people.intersection(subjects)) < 2:
            raise ValueError("A multi-person shot needs two visible subjects")
    if behavior in {"track", "pan"} and not shot["uncertain"]:
        raise ValueError("Still-frame evidence cannot verify a moving crop path")
