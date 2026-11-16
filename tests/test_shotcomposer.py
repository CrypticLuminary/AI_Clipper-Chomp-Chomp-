"""Exercise Director edits against decoded frame IDs, selected audio and source evidence."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import wave

import numpy as np
import pytest

from chomp.artifacts import artifact_key, digest_file
from chomp.cli import main
from chomp.clipwork import approve
from chomp.director.contract import read_json, schema
from chomp.director.evidence import build_bundle
from chomp.media import render_preview
from chomp.shotcomposer.adapters import from_editorial
from chomp.shotcomposer.compiler import compile_plan
from chomp.shotcomposer.contract import fraction, frame_index, validate
from chomp.shotcomposer.workflow import base_plan, prepare, render, slot, source_metadata, verify_source
from test_stage1_integration import ffmpeg, needs_ffmpeg


jsonschema = pytest.importorskip("jsonschema")


def region(identifier, box, *, role="primary_action", subject=None,
           start=0, end=2400, basis="human_interval"):
    return {
        "id": identifier, "subject_id": subject or identifier, "role": role, "box": list(box),
        "start_ms": start, "end_ms": end, "support": "observed", "visibility": "visible",
        "basis": basis, "reviewer": "fixture", "reference": "Known generated image contents",
        "minimum_output_px": 4,
    }


def shot(identifier, start, end, slots, *, layout="gameplay_full", duration=0):
    return {
        "id": identifier, "start_ms": start, "end_ms": end, "layout": layout,
        "reason": "Show the selected source area", "evidence_refs": [s["region_id"] for s in slots],
        "slots": slots, "transition": {"kind": "dissolve" if duration else "cut", "duration_ms": duration},
        "graphics": [],
    }


def evidence_for(source):
    return {
        "schema_version": "chomp-shot-evidence/1", "source": source, "rights_ref": "synthetic",
        "regions": [
            region("left", [0, 0, 80, 96]),
            region("right", [80, 0, 80, 96]),
            region("key", [24, 28, 26, 30], role="object"),
        ],
        "facts": [], "scene_cuts_ms": [], "tracking": [],
    }


def timing_plan(evidence, rate="30"):
    plan = base_plan(evidence, 213, 1731, width=80, height=96, fps=rate)
    plan["shots"] = [
        shot("first", 213, 701, [slot("left", [0, 0, 80, 96])]),
        shot("second", 701, 1103, [slot("right", [0, 0, 80, 96])]),
        shot("return", 1103, 1731, [slot("left", [0, 0, 80, 96])], duration=191),
    ]
    return plan


@pytest.fixture
def contract():
    evidence = evidence_for({
        "sha256": "0" * 64, "duration_ms": 2400, "width": 160, "height": 96, "rotation": 0,
        "sar": {"num": 1, "den": 1}, "frame_rate": {"num": 30, "den": 1},
        "video_stream": 0, "audio_stream": 0,
    })
    return timing_plan(evidence), evidence


def test_schemas_validate_as_draft_2020_12():
    for name in ("shot_plan", "shot_evidence"):
        jsonschema.Draft202012Validator.check_schema(schema(name))


@pytest.mark.parametrize("mutate", [
    lambda plan: plan.update(source_sha256="1" * 64),
    lambda plan: plan.update(evidence_sha256="1" * 64),
    lambda plan: plan["shots"][0].update(start_ms=214),
    lambda plan: plan["shots"][0].update(start_ms=213.0),
    lambda plan: plan["shots"][0].update(start_ms=True),
    lambda plan: plan["shots"][0]["slots"][0].update(filter="movie=/arbitrary"),
    lambda plan: plan["shots"][0]["slots"][0].update(rect=[1, 0, 78, 96]),
    lambda plan: plan["shots"][0]["slots"][0].update(zoom=[{"at_ms": 213, "scale": float("nan")}]),
    lambda plan: plan["shots"][0]["slots"][0].update(crop_path=[[213, 0, 0], [213, 0, 0], [701, 0, 0]]),
    lambda plan: plan["shots"][0]["slots"][0].update(zoom=[{"at_ms": 701, "scale": 1.1}]),
])
def test_invalid_plans_fail_before_compilation(contract, mutate):
    plan, evidence = contract
    mutate(plan)
    with pytest.raises(ValueError):
        compile_plan(plan, evidence)


def test_snapshot_and_cut_do_not_establish_interval_visibility(contract):
    plan, evidence = contract
    evidence["regions"][0].update(basis="sample")
    evidence["regions"][1].update(end_ms=1160)
    evidence["scene_cuts_ms"] = [1250]
    plan["evidence_sha256"] = artifact_key(evidence)
    report = validate(plan, evidence).as_dict()
    assert report["status"] == "blocked"
    assert {p["code"] for p in report["problems"]} >= {
        "interval_visibility_unverified", "region_outside_verified_interval", "dissolve_crosses_source_cut",
    }


def test_swept_protected_region_cannot_pass_through_inset(contract):
    _, evidence = contract
    evidence["regions"].append(region("point", [49, 28, 4, 20], role="object"))
    plan = base_plan(evidence, 0, 2400, width=80, height=96)
    moving = slot("left", [0, 0, 80, 96], required=["point"])
    moving["crop_path"] = [[0, 0, 0], [2400, 40, 0]]
    inset = slot("right", [24, 20, 14, 36], name="inset")
    plan["shots"] = [shot("main", 0, 2400, [moving, inset], layout="picture_in_picture")]
    assert "overlay_crosses_subject_motion" in {p["code"] for p in validate(plan, evidence).problems}


def test_loss_is_distinct_from_confirmed_source_overlay(contract):
    plan, evidence = contract
    track = {
        "source_sha256": plan["source_sha256"], "track_sha256": "4" * 64,
        "subject_id": "left", "tracker_revision": 3,
        "observations": [{"at_ms": 350, "state": "tracker_lost", "box": None,
                          "reason": "insufficient_pixel_support", "confidence": 0}],
        "source_spans": [{"start_ms": 0, "end_ms": 2400, "overlay": None, "cut": False,
                          "usable_fraction": 1}],
    }
    evidence["tracking"] = [track]
    plan["evidence_sha256"] = artifact_key(evidence)
    result = validate(plan, evidence)
    assert result.renderable
    assert "reviewed_tracking_gap" in {p["code"] for p in result.problems}
    track["source_spans"][0]["overlay"] = [20, 20, 40, 40]
    plan["evidence_sha256"] = artifact_key(evidence)
    assert "protected_region_covered_by_source_overlay" in {p["code"] for p in validate(plan, evidence).problems}
    assert not validate(plan, evidence).renderable


def test_delegated_edit_request_requires_explicit_optional_status(contract):
    plan, evidence = contract
    plan["capability_requests"] = [{"operator": "time.replay", "owner": "FX05",
                                    "reason": "Show a second look", "required": True,
                                    "evidence_refs": ["left"]}]
    assert not validate(plan, evidence).renderable
    plan["capability_requests"][0]["required"] = False
    assert validate(plan, evidence).renderable
    assert validate(plan, evidence).problems[-1]["severity"] == "delegated"
    plan["capability_requests"][0]["owner"] = "FX04"
    assert validate(plan, evidence).problems[-1]["code"] == "invalid_capability_request"


def _pulse(path: Path, frequency: int, delay: float) -> None:
    clock = np.arange(48_000 * 3) / 48_000
    sound = np.sin(2 * np.pi * frequency * clock) * ((delay <= clock) & (clock < delay + .050))
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        out.writeframes((sound * 23000).astype("<i2").tobytes())


def make_source(root: Path, rate: str, *, vfr: bool = False, delay: float = 0) -> Path:
    """Both views encode the same frame number in luma; the right is 100 brighter."""
    raw = root / "frame-clock.yuv"
    with raw.open("wb") as file:
        chroma = bytes([128]) * (160 * 96 // 2)
        for i in range(182):
            value = 20 + i % 94
            luma = np.empty((96, 160), np.uint8)
            luma[:, :80], luma[:, 80:] = value, value + 100
            luma[28:58, 24:50] = 222
            file.write(luma.tobytes() + chroma)
    first, second = root / "other.wav", root / "selected.wav"
    _pulse(first, 440, .46)
    _pulse(second, 997, .82)
    source = root / "two views & audio.mp4"
    filter_options = ["-vf", "select='not(eq(mod(n,5),4))'", "-fps_mode", "vfr"] if vfr else []
    ffmpeg("-f", "rawvideo", "-pixel_format", "yuv420p", "-video_size", "160x96",
           "-framerate", rate, "-i", str(raw), "-i", str(first), "-itsoffset", str(delay), "-i", str(second),
           "-map", "0:v", "-map", "1:a", "-map", "2:a", "-t", "2.400", *filter_options,
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0", "-c:a", "aac", str(source))
    return source


def _lumas(path: Path, width: int = 80, height: int = 96) -> np.ndarray:
    data = ffmpeg("-i", str(path), "-map", "0:v:0", "-an", "-pix_fmt", "yuv420p",
                  "-fps_mode", "passthrough", "-f", "rawvideo", "-threads:v", "1", "-")
    yuv = np.frombuffer(data, np.uint8).reshape(-1, width * height * 3 // 2)
    return yuv[:, :width*height].reshape(-1, height, width)


def _audio(path: Path) -> bytes:
    return ffmpeg("-i", str(path), "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", "48000", "-")


@needs_ffmpeg
@pytest.mark.parametrize("rate,vfr,delay", [
    ("24000/1001", False, 0), ("25", False, 0), ("30000/1001", False, 0),
    ("30", False, 0), ("60", False, 0), ("30", True, .22),
])
def test_director_cut_and_dissolve_keep_frame_clock_and_selected_audio(tmp_path, rate, vfr, delay):
    source = make_source(tmp_path, rate, vfr=vfr, delay=delay)
    evidence = evidence_for(source_metadata(source, 1))
    plan = timing_plan(evidence, rate)
    out = tmp_path / "director"
    report = render(source, out, plan, evidence)
    assert report["status"] == "rendered_pending_review", report
    preview = out / "preview.mp4"
    assert report["preview_sha256"] == digest_file(preview)
    clip_start, clip_end = plan["start_ms"], plan["end_ms"]
    reference = deepcopy(plan)
    reference["shots"] = [shot("hold", clip_start, clip_end, [slot("left", [0, 0, 80, 96])])]
    render_preview(source, tmp_path / "hold.mp4", clip_start, clip_end, 80, 96, audio_stream=1,
                   shot_plan=reference, shot_evidence=evidence)
    clock = fraction(plan["canvas"]["fps"])
    f0, f1, f2 = (frame_index(t, clip_start, clock) for t in (701, 1103, 1294))
    left = np.median(_lumas(tmp_path / "hold.mp4")[:, 12:84, 10:70], axis=(1, 2))
    actual = np.median(_lumas(preview)[:, 12:84, 10:70], axis=(1, 2))
    assert len(actual) == len(left) == frame_index(clip_end, clip_start, clock)
    expected = np.zeros(len(left))
    expected[f0:f1] = 100
    expected[f1:f2] = 100 * (1 - np.arange(f2-f1) / (f2-f1))
    assert np.max(np.abs(actual - left - expected)) <= 4
    picture_frames = np.unwrap(left, period=94)
    diffs = np.diff(picture_frames)
    if not vfr:
        assert np.max(np.abs(diffs - 1)) <= 2
        assert abs(left[0] - (20 + round(Fraction(clip_start, 1000) * Fraction(rate)))) <= 2
        assert abs((picture_frames[-1] - picture_frames[0]) - (len(left)-1)) <= 2
    audio = _audio(preview)
    assert audio == _audio(tmp_path / "hold.mp4")
    samples = np.frombuffer(audio, "<i2").astype(float)
    strongest = np.flatnonzero(np.abs(samples) > 3500)
    assert .590 + delay < strongest[0] / 48000 < .630 + delay
    peaks = np.abs(np.fft.rfft(samples[strongest[0]:strongest[-1]+1]))
    dominant = np.argmax(peaks) * 48000 / (strongest[-1] - strongest[0] + 1)
    assert dominant == pytest.approx(997, abs=25)


@needs_ffmpeg
def test_prepare_cli_and_source_snapshot_guards(tmp_path, capsys):
    source = make_source(tmp_path, "30")
    before = digest_file(source)
    pack = tmp_path / "pack"
    assert main(["director", "shots", "prepare", "--source", str(source), "--out", str(pack),
                 "--start-ms", "210", "--end-ms", "1670", "--rights-ref", "synthetic",
                 "--acknowledge-permission"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "prepared"
    plan = read_json(pack / "starter.shots.json")
    evidence = read_json(pack / "evidence.json")
    assert plan["evidence_sha256"] == artifact_key(evidence)
    assert plan["shots"][0]["layout"] == "full_source"
    assert validate(plan, evidence).renderable
    assert digest_file(source) == before
    with pytest.raises(FileExistsError):
        prepare(source, pack, start_ms=210, end_ms=1670, rights_ref="synthetic")
    evidence["source"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Source bytes"):
        verify_source(source, evidence)


@needs_ffmpeg
def test_zoom_returns_to_held_view_and_outline_is_timed(tmp_path):
    source = make_source(tmp_path, "30")
    evidence = evidence_for(source_metadata(source))
    plan = base_plan(evidence, 0, 2200, width=80, height=96)
    anchor = slot("left", [0, 0, 80, 96], fit="cover", required=["key"])
    zoomed = deepcopy(anchor)
    zoomed["zoom"] = [{"at_ms": at, "scale": scale}
                      for at, scale in [(0, 1), (450, 1.30), (800, 1.30), (1200, 1)]]
    plan["shots"] = [shot("detail", 0, 1200, [zoomed]), shot("hold", 1200, 2200, [anchor])]
    plan["shots"][1]["graphics"] = [{"kind": "outline", "slot_id": "main", "region_id": "key",
                                    "start_ms": 1500, "end_ms": 1800, "thickness": 3, "color": "#000000",
                                    "opacity": .45, "reason": "Emphasize the observed detail",
                                    "evidence_refs": ["key"]}]
    out = tmp_path / "motion"
    assert render(source, out, plan, evidence)["status"] == "rendered_pending_review"
    frames = _lumas(out / "preview.mp4")
    # The source's bright patch occupies 26x30 pixels at 1x scale.
    areas = np.count_nonzero(frames[:, 16:72, 16:64] > 195, axis=(1, 2))
    assert len(areas) == 66
    assert abs(areas[0] - 780) < 35
    assert areas[16] > 1.48 * areas[0]
    y, x = np.where(frames[16] > 195)
    assert 32 <= x.mean() <= 38 and 39 <= y.mean() <= 44
    assert np.diff(frames[:36, 8, 8].astype(int)).min() >= 0
    assert int(frames[30, 8, 8]) - int(frames[0, 8, 8]) >= 28
    assert abs(areas[40] - areas[0]) < 35
    assert areas[48] + 150 < areas[40]
    assert abs(areas[57] - areas[0]) < 35
    approval = approve(out / "review.json", tmp_path / "acceptance.json", reviewer="fixture", clip_index=1, watched=True)
    assert approval["artifact_sha256"]["shot_evidence"] == digest_file(out / "evidence.json")
    (out / "evidence.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="shot_evidence changed"):
        approve(out / "review.json", tmp_path / "stale.json", reviewer="fixture", clip_index=1, watched=True)


@needs_ffmpeg
def test_pointer_is_source_linked_and_refuses_caption_conflict(tmp_path):
    source = make_source(tmp_path, "30")
    evidence = evidence_for(source_metadata(source))
    plan = base_plan(evidence, 0, 1400, width=80, height=96)
    plan["shots"] = [shot("one", 0, 1400, [slot("left", [0, 0, 80, 96], required=["key"])])]
    plan["shots"][0]["graphics"] = [{"kind": "pointer", "slot_id": "main", "region_id": "key",
                                    "start_ms": 400, "end_ms": 800, "thickness": 3, "color": "#FFFFFF",
                                    "opacity": .45, "reason": "Point at the white reference patch",
                                    "evidence_refs": ["key"]}]
    out = tmp_path / "arrow"
    assert render(source, out, plan, evidence)["status"] == "rendered_pending_review"
    frames = _lumas(out / "preview.mp4")
    assert int(frames[15, 43, 12]) > int(frames[15, 32, 12]) + 50
    assert abs(int(frames[31, 43, 12]) - int(frames[31, 32, 12])) < 5
    tiny = deepcopy(plan)
    tiny["shots"][0]["graphics"][0].update(start_ms=401, end_ms=405)
    assert "subframe_graphic" in {p["code"] for p in validate(tiny, evidence).problems}
    plan["caption_exclusions"] = [[0, 30, 21, 26], [56, 30, 24, 26]]
    assert "pointer_has_no_clear_placement" in {p["code"] for p in validate(plan, evidence).problems}


@needs_ffmpeg
def test_oriented_anamorphic_ten_bit_source_keeps_frame_and_audio(tmp_path):
    source = make_source(tmp_path, "25")
    anamorphic = tmp_path / "anamorphic.mp4"
    ffmpeg("-i", str(source), "-vf", "crop=159:95:0:0:exact=1,setsar=2/1,format=yuv444p10le",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-c:a", "copy", str(anamorphic))
    rotated = tmp_path / "rotated.mp4"
    if b"-display_rotation" in ffmpeg("-h", "full"):
        ffmpeg("-display_rotation:v:0", "90", "-i", str(anamorphic), "-map", "0", "-c", "copy", str(rotated))
    else:
        ffmpeg("-i", str(anamorphic), "-map", "0", "-c", "copy", "-metadata:s:v:0", "rotate=90", str(rotated))
    pack = tmp_path / "rotated-pack"
    prepare(rotated, pack, start_ms=0, end_ms=1200, rights_ref="synthetic", width=80, height=160, fps="25")
    evidence = read_json(pack / "evidence.json")
    assert (evidence["source"]["width"], evidence["source"]["height"]) == (95, 159)
    assert evidence["source"]["sar"] == {"num": 1, "den": 2}
    plan = read_json(pack / "starter.shots.json")
    out = tmp_path / "oriented"
    assert render(rotated, out, plan, evidence)["status"] == "rendered_pending_review"
    frames = _lumas(out / "preview.mp4", width=80, height=160)
    assert frames.shape == (30, 160, 80)
    # The source is more slender after display rotation and SAR correction.
    assert np.ptp(frames[9, :, 2]) < 8 and np.ptp(frames[9, :, 40]) > 70


@needs_ffmpeg
def test_later_zoom_and_inset_advance_on_the_same_frame_clock(tmp_path):
    source = make_source(tmp_path, "30")
    evidence = evidence_for(source_metadata(source))
    plan = base_plan(evidence, 0, 1500, width=80, height=96, fps="30")
    main_view = slot("left", [0, 0, 80, 96], fit="cover", required=["key"])
    body = deepcopy(main_view)
    body["zoom"] = [{"at_ms": 400, "scale": 1}, {"at_ms": 750, "scale": 1.20}, {"at_ms": 1500, "scale": 1.20}]
    inset = slot("right", [56, 72, 22, 22], name="inset")
    plan["shots"] = [
        shot("first", 0, 400, [main_view]),
        shot("both", 400, 1500, [body, inset], layout="picture_in_picture"),
    ]
    out = tmp_path / "pip"
    assert render(source, out, plan, evidence)["status"] == "rendered_pending_review"
    frames = _lumas(out / "preview.mp4")
    assert len(frames) == 45
    assert abs(int(frames[11, 84, 67]) - int(frames[11, 84, 14])) < 5
    offsets = frames[12:, 84, 67].astype(int) - frames[12:, 84, 14].astype(int)
    assert np.max(np.abs(offsets - 100)) <= 5


@needs_ffmpeg
def test_lower_saved_director_storyboard_and_render_its_choices(tmp_path, capsys):
    source = make_source(tmp_path, "30")
    annotations = tmp_path / "regions.json"
    annotations.write_text(json.dumps({
        str(time): [{"subject_id": subject, "kind": "action", "box": box, "confidence": 1,
                     "origin": "operator_annotation", "verified": True}]
        for time, subject, box in [(430, "left", [0, 0, 80, 96]), (1200, "right", [80, 0, 80, 96])]
    }), encoding="utf-8")
    bundle_path = build_bundle(source, tmp_path / "bundle", case_id="sample", split="development",
                               rights_ref="synthetic", candidate_id="event", start_ms=210, end_ms=1670,
                               anchor_ms=1200, frame_times=(430, 1200), regions=annotations)
    bundle = read_json(bundle_path)
    evidence = evidence_for(source_metadata(source))
    evidence["regions"].append(region("source_frame", [0, 0, 160, 96], role="source", basis="source_bounds"))
    editorial = {
        "schema_version": "chomp-editorial/1", "decision": "select", "decision_reason": "The brighter area follows.",
        "categories": ["other"], "confidence": .9, "claims": [],
        "moment": {"start_ms": 210, "end_ms": 1670, "anchor_ms": 1200, "evidence_ids": ["f2"]},
        "beats": [{"start_ms": 900, "end_ms": 1500, "role": "event", "description": "A brighter view.", "evidence_ids": ["f2"]}],
        "shots": [{"start_ms": a, "end_ms": b, "layout": "main_only", "target_subject_ids": [target],
                   "required_region_ids": [f"{frame}-r1"], "crop_behavior": "hold", "reason": f"Show {target}",
                   "evidence_ids": [frame], "uncertain": False}
                  for a, b, target, frame in [(210, 830, "left", "f1"), (830, 1670, "right", "f2")]],
        "title": None, "hook": None, "captions": {"mode": "verify_speech", "emphasis_word_ids": []},
        "requires_visual_verification": False, "unresolved": [],
        "review_status": "pending", "publish_status": "disabled",
        "provenance": {"bundle_sha256": bundle["evidence_hash"], "source_sha256": bundle["source"]["sha256"],
                       "mode": "frames", "provider": "fixture", "model": "fixture", "cli_version": None, "prompt_sha256": "0" * 64},
    }
    bindings = {"schema_version": "chomp-shot-bindings/1", "editorial_plan_sha256": artifact_key(editorial),
                "evidence_sha256": artifact_key(evidence), "regions": {"f1-r1": "left", "f2-r1": "right"},
                "reviewer": "fixture", "review_reference": "Known synthetic scene"}
    bad = {**bindings, "regions": {"f1-r1": "source_frame", "f2-r1": "right"}}
    with pytest.raises(ValueError, match="role or subject"):
        from_editorial(editorial, bundle, evidence, bad, width=80, height=96)
    paths = {}
    for name, data in (("editorial", editorial), ("evidence", evidence), ("bindings", bindings)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(data), encoding="utf-8")
    pack = tmp_path / "director-pack"
    assert prepare(source, pack, start_ms=210, end_ms=1670, rights_ref="synthetic", width=80, height=96,
                   bundle_path=bundle_path, editorial_path=paths["editorial"], evidence=evidence)["verified_images"] == 2
    assert digest_file(pack / "frames" / "f2.png") == bundle["visual_samples"][1]["sha256"]
    lowered = tmp_path / "lowered"
    assert main(["director", "shots", "from-director", "--source", str(source), "--bundle", str(bundle_path),
                 "--editorial", str(paths["editorial"]), "--evidence", str(paths["evidence"]),
                 "--bindings", str(paths["bindings"]), "--width", "80", "--height", "96",
                 "--out", str(lowered)]) == 0, capsys.readouterr().out
    plan = read_json(lowered / "plan.json")
    assert plan["provenance"]["editorial_plan_sha256"] == artifact_key(editorial)
    out = tmp_path / "review"
    assert main(["shots", "render", "--source", str(source), "--plan", str(lowered / "plan.json"),
                 "--evidence", str(lowered / "evidence.json"), "--out", str(out), "--experimental"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["status"] == "rendered_pending_review"
    luma = _lumas(out / "preview.mp4")
    before, after = luma[18:20, 0, 5]
    assert 95 <= int(after) - int(before) <= 107
