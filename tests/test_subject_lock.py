"""Deterministic coverage of seeded identity, loss and exact-range Shorts."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from chomp.artifacts import source_identity
from chomp.camera_motion import camera_path
from chomp.cli import main
from chomp.evaluation.subject_compare import evaluate
from chomp.manual import rerender
from chomp.media import LayoutSegment
from chomp.subject_lock import SEED_SCHEMA, Seed, SourceSpan, SubjectPlan, follow_subject, overlap_fraction, read_subject_plan
from chomp.subject_view import subject_windows

from test_stage1_integration import ffmpeg, needs_ffmpeg

SIZE = (192, 128)
TEXTURE = np.random.default_rng(17).integers(30, 245, size=(34, 34), dtype=np.uint8)


def _image(x=None, other=None, *, overlay=False):
    frame = np.full(SIZE[::-1], 28, np.uint8)
    for left in (x, other):
        if left is not None:
            frame[54:88, left:left + 34] = TEXTURE
    if overlay:
        frame[:, 30:155] = 152
        frame[20:116, 55:130:2] = 105
    return frame


def _follow(images, spans=None, seeds=None):
    pytest.importorskip("cv2")
    end = len(images) * 50
    plan = SubjectPlan("fixture", "point-group",
                       seeds or (Seed(0, (45, 54, 34, 34), "fixture"),), (), True)
    return follow_subject(((i * 50, image) for i, image in enumerate(images)), plan,
                          spans or (SourceSpan(0, end, None),), SIZE, SIZE)


def test_seeded_target_moves_with_consistent_pixels():
    rows = _follow([_image(x) for x in range(45, 75, 3)])
    assert all(row.box is not None and row.state == "visible" for row in rows)
    for i, row in enumerate(rows):
        assert overlap_fraction((45 + i * 3, 54, 34, 34), row.box) > .88


def test_single_match_does_not_publish_target_until_confirmed():
    rows = _follow([_image(45), _image(48), _image(), _image(79), _image(79), _image(79), _image(81),
                    _image(83), _image(84)])
    assert rows[2].state == "tracker_lost" and rows[2].box is None
    recovered = [row for row in rows if row.state == "redetected"]
    assert len(recovered) == 1 and recovered[0].at_ms >= 250
    assert all(row.box is None for row in rows[2:recovered[0].at_ms // 50])
    assert rows[-1].state == "visible" and overlap_fraction((84, 54, 34, 34), rows[-1].box) > .88


def test_competing_same_appearance_is_ambiguous_after_loss():
    rows = _follow([_image(45), _image(46), _image()] + [_image(30, 88)] * 54)
    assert all(row.state == "tracker_lost" and row.box is None for row in rows[2:])
    assert rows[-1].reason == "bounded_search_exhausted"


def test_source_cut_requires_a_new_observation_even_with_matching_pixels():
    spans = (SourceSpan(0, 100, None), SourceSpan(100, 350, None, cut=True))
    images = [_image(45)] * 7
    rows = _follow(images, spans, (Seed(0, (45, 54, 34, 34), "fixture"),
                                  Seed(250, (45, 54, 34, 34), "reviewed")))
    assert all(row.box is None and row.reason == "scene_cut_requires_verified_seed" for row in rows[2:5])
    assert rows[5].reason == "seed:reviewed" and rows[-1].state == "visible"


def test_jay3_03_subject_unavailable_when_source_overlay_hides_gameplay():
    """JAY3-03: Subject unavailable / insufficient gameplay visibility."""
    spans = (SourceSpan(0, 150, None), SourceSpan(150, 2300, (30, 0, 125, 128), usable_fraction=.31),
             SourceSpan(2300, 2800, None))
    rows = _follow([_image(45), _image(47), _image(50)] + [_image(overlay=True)] * 43
                   + [_image(51)] * 10, spans)
    assert all(row.state == "covered_by_source_overlay" and row.box is None and row.confidence == 0.
               for row in rows[3:46])
    assert any(row.state == "redetected" for row in rows[46:])
    assert rows[-1].state == "visible"


def test_viewport_retains_recent_space_when_evidence_stops():
    boxes = [
        {"at_ms": 0, "box": (45, 18, 16, 25), "confidence": 1.},
        {"at_ms": 250, "box": (111, 22, 21, 29), "confidence": .9},
        {"at_ms": 500, "box": (160, 22, 21, 29), "confidence": .15},
        {"at_ms": 750, "box": None, "confidence": 0.},
    ]
    protect = {"id": "aim", "start_ms": 0, "end_ms": 250, "box": (85, 40, 9, 9)}
    track = {"observations": boxes, "protected_regions": [protect]}
    initial, path, _ = subject_windows(track, [0, 250, 500, 750], SIZE, SIZE, (0, 0, *SIZE),
                                       [(0, 0, *SIZE)], .60, "pan")
    assert overlap_fraction(boxes[0]["box"], initial) == overlap_fraction(protect["box"], initial) == 1.
    assert len(set(path)) == 1
    fast = camera_path((0, 0), [(80, 0)] * 3 + [None] * 6, (80, 90), ((0, 100), (0, 0)), "track")
    assert fast[-1] == fast[-2] and fast[-1][0] < 40


def test_plan_is_source_bound_and_accepts_a_point(tmp_path):
    path = tmp_path / "seed.json"
    data = {"schema_version": SEED_SCHEMA, "source_id": "fixture", "subject_id": "aim",
            "seeds": [{"at_ms": 200, "point": [0, 40]}], "source_has_overlay": False}
    path.write_text(json.dumps(data), encoding="utf-8")
    plan = read_subject_plan(path, "fixture", 100, 500, SIZE)
    assert plan.seeds[0].box == (0, 34, 7, 13) and not plan.source_has_overlay
    with pytest.raises(ValueError, match="source SHA"):
        read_subject_plan(path, "other-media", 100, 500, SIZE)
    data["seeds"][0]["box"] = [1, 1, 40, 40]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="one box or point"):
        read_subject_plan(path, "fixture", 100, 500, SIZE)


def test_benchmark_separates_visibility_from_the_crop_and_identity():
    labels = [
        {"at_ms": 100, "state": "visible", "box": (40, 10, 20, 20),
         "protected_regions": {"aim": (60, 10, 20, 20)}, "distractor_boxes": [(80, 10, 20, 20)]},
        {"at_ms": 200, "state": "covered_by_source_overlay"},
    ]
    track = {"sample_step_ms": 50, "observations": [
        {"at_ms": 100, "box": (80, 10, 20, 20), "state": "visible", "reason": "flow_consensus", "confidence": .9},
        {"at_ms": 200, "box": (40, 10, 20, 20), "state": "visible", "reason": "flow_consensus", "confidence": .9},
    ]}
    plan = (LayoutSegment(100, 300, "gameplay-only", gameplay_roi=(0, 0, 50, 100)),)
    scores = evaluate(labels, plan, track, SIZE, 100, 300)
    assert scores["mean_target_fraction"] == .5
    assert scores["mean_protected_fraction"] == 0
    assert scores["wrong_location_labels"] == scores["false_presence_labels"] == scores["known_distractor_matches"] == 1
    assert scores["state_duration_ms"] == {"visible": 200}


@needs_ffmpeg
def test_subject_plan_cli_and_caption_style_rerender_keep_verified_edit(tmp_path, capsys):
    cv2 = pytest.importorskip("cv2")
    source = tmp_path / "subject fixture.mp4"
    for i in range(32):
        assert cv2.imwrite(str(tmp_path / f"sample-{i:03d}.png"), _image(45 + i // 2))
    ffmpeg("-framerate", "20", "-i", str(tmp_path / "sample-%03d.png"),
           "-f", "lavfi", "-i", "sine=duration=1.6", "-t", "1.6", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-crf", "14", "-c:a", "aac", str(source))
    seed = tmp_path / "subject.json"
    seed.write_text(json.dumps({"schema_version": SEED_SCHEMA, "source_id": source_identity(source),
                                "subject_id": "moving-patch", "source_has_overlay": False,
                                "seeds": [{"at_ms": 0, "box": [45, 54, 34, 34]}]}), encoding="utf-8")
    out = tmp_path / "runs"
    assert main(["--input", str(source), "--output", str(out), "--rights-ref", "fixture",
                 "--acknowledge-permission", "--start", "0", "--end", "1.6",
                 "--layout", "shorts", "--subject-plan", str(seed), "--render"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    path = Path(manifest["edit_spec"])
    edit = json.loads(path.read_text(encoding="utf-8"))
    track = edit["subject_tracking"]
    assert track["subject_id"] == manifest["subject_id"] == "moving-patch"
    assert all(row["state"] == "visible" and row["planned_target_fraction"] >= .9 for row in track["observations"])
    assert edit["segments"][0]["layout"] == "gameplay-only"
    revised = rerender(path, out, caption_position="middle")
    saved = json.loads(Path(revised["edit_spec"]).read_text(encoding="utf-8"))
    assert saved["subject_tracking"] == track and saved["segments"] == edit["segments"]
