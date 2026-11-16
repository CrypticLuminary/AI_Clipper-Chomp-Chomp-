"""Regressions for the Shorts layout rules and its editable crop paths."""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from chomp.clipwork import layout_segments
from chomp.editorial import validate_judgment
from chomp.manual import render_manual_preview, rerender
from chomp.media import crop_position
from chomp.reframe import Camera, Sample, _action_windows, _refine_switch, _visible_regions, camera_container
from test_stage1_integration import ffmpeg, needs_ffmpeg


@pytest.mark.parametrize("corner", ["top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right"])
def test_camera_panel_has_no_source_corner_assumption(corner):
    width, height = 700, 400
    cw, ch = 168, 108
    x = {"left": 15, "center": (width - cw) // 2, "right": width - cw - 13}[corner.split("-")[1]]
    y = 13 if corner.startswith("top") else height - ch - 11
    image = np.full((height, width, 3), (37, 191, 105), dtype=np.uint8)
    image[y:y + ch, x:x + cw] = (160, 35, 158)
    face = (x + 65, y + 31, 42, 49)
    fx, fy, fw, fh = face
    image[fy:fy + fh, fx:fx + fw] = (72, 103, 156)
    detected = camera_container(image, face)
    assert detected is not None
    assert detected.box == (x, y, cw, ch)


def test_action_pan_preserves_aspect_and_avoids_the_webcam_domain():
    pytest.importorskip("cv2")
    picture = (0, 0, 160, 90)
    source_camera = (131, 0, 29, 32)
    maps = []
    for t in range(18):
        heat = np.full((90, 160), .02, dtype=np.float32)
        center = 20 + 5 * t
        heat[28:62, center - 6:center + 7] = 12.
        heat[0:32, 131:] = 0
        maps.append(heat)
    view, path, _ = _action_windows(maps, picture, _visible_regions(picture, source_camera), .80)
    assert view[2] / view[3] == pytest.approx(.8, abs=.02)
    assert path[-1][0] - path[0][0] >= 28
    for i in (0, len(maps) - 1):
        center = 20 + 5 * i
        assert path[i][0] <= center - 6 and path[i][0] + view[2] >= center + 7
    for x, y in path:
        assert x + view[2] <= source_camera[0] or y >= 32


def test_rerender_clips_and_interpolates_gameplay_keyframes():
    [seg] = layout_segments([{
        "start_ms": 1000, "end_ms": 7000, "layout": "gameplay-only",
        "gameplay_roi": [25, 0, 96, 170],
        "gameplay_path": [[1000, 25, 0], [3000, 75, 0], [7000, 155, 4]],
    }], 2000, 5500)
    assert seg.gameplay_path == ((2000, 50, 0), [3000, 75, 0], (5500, 125, 2))
    assert crop_position(seg.gameplay_path, 5000) == (115, 2)


def test_camera_switch_ignores_gameplay_flash(monkeypatch):
    pytest.importorskip("cv2")
    small = Camera((241, 50, 29, 34), (215, 31, 105, 74), .85)
    large = Camera((180, 36, 53, 61), (151, 13, 169, 138), .9)
    frames = np.zeros((35, 180, 320, 3), np.uint8)
    frames[3, :, 140:] = 255  # A brighter cut in the game precedes the actual camera change.
    frames[25:, 0, 0] = 64

    def read_frames(_source, lo, hi, _width, _height, rate):
        return frames[:(hi - lo) * rate // 1000]

    monkeypatch.setattr("chomp.reframe._read_frames", read_frames)

    class Detector:
        def detect(self, frame):
            face = large.face if frame[0, 0, 0] else small.face
            return 1, np.array([face], dtype=np.float32)

    switch_ms = _refine_switch(Path("unused.mp4"), 2000, small, large, (320, 180),
                               1000, 3000, Fraction(60), Detector())
    assert switch_ms == 2100


@needs_ffmpeg
def test_zoom_keeps_same_output_slot_and_excludes_camera_from_action(tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    source = tmp_path / "zoom.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=blue:size=320x180:rate=30:duration=4",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
           "-vf", "drawbox=x=15:y=14:w=100:h=63:color=red:t=fill:enable='lt(t,2)',"
                  "drawbox=x=85:y=3:w=235:h=133:color=red:t=fill:enable='gte(t,2)'",
           "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(source))
    planned = []
    for i in range(16):
        box = (15, 14, 100, 63) if i < 8 else (85, 3, 235, 133)
        face = (48, 26, 28, 29) if i < 8 else (213, 34, 52, 54)
        image = np.full((180, 320, 3), (234, 0, 0), dtype=np.uint8)
        x, y, w, h = box
        image[y:y + h, x:x + w] = (0, 0, 234)
        planned.append(Sample(250 * i, image, [Camera(face, box, .85)]))

    monkeypatch.setattr("chomp.reframe._sample", lambda *_: (planned, (320, 180)))
    out = tmp_path / "exports"
    report = render_manual_preview(source, out, rights_ref="fixture", start_s=0, end_s=4, layout="shorts")
    edit = Path(report["edit_spec"])
    plan = json.loads(edit.read_text(encoding="utf-8"))
    segments = layout_segments(plan["segments"], 0, 4000)
    assert len(segments) == 2
    assert segments[0].end_ms == pytest.approx(2000, abs=40)
    assert segments[0].facecam_roi != segments[1].facecam_roi
    assert all(s.camera_fraction == .30 and s.camera_slot == "top" for s in segments)
    warning = "An enlarged webcam leaves little visible gameplay in part of the cut."
    assert warning in report["warnings"]

    def unexpected_analysis(*args, **kwargs):
        pytest.fail("Rerender should reuse the saved camera and gameplay tracks")

    monkeypatch.setattr("chomp.manual.cached_shorts_plan", unexpected_analysis)
    revision = rerender(edit, out, caption_position="middle")
    assert revision["facecam_detection"] == plan["facecam_detection"]
    assert warning in revision["warnings"]
    for t in (1, 3):
        data = ffmpeg("-ss", str(t), "-i", revision["preview"], "-frames:v", "1", "-f", "rawvideo",
                      "-pix_fmt", "rgb24", "pipe:1")
        rgb = np.frombuffer(data, dtype=np.uint8).reshape(960, 540, 3)
        assert np.mean(rgb[40:250, :, 0] > 170) > .85
        assert np.mean(rgb[320:, :, 2] > 170) > .97
        assert np.mean(rgb[320:, :, 0] > 170) < .01


def test_model_title_must_match_cited_payoff():
    context = {"anchor_ms": 1000, "utterances": [
        {"id": "s0", "start_ms": 0, "end_ms": 2000, "text": "I found the last key inside."},
    ]}
    result = {"verdict": "select", "categories": ["discovery"],
              "evidence": {"setup": [], "event": ["s0"], "payoff": ["s0"], "reaction": []},
              "scores": {"category_payoff": 4, "standalone_clarity": 4, "hook_strength": 4, "distinctiveness": 4},
              "requires_visual_verification": True, "rationale": "Found the object."}
    assert validate_judgment({**result, "title": "The Last Key Inside"}, context)["title"] == "The Last Key Inside"
    assert validate_judgment({**result, "title": "He Beat The Final Boss"}, context)["title"] is None
