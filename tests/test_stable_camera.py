"""Regressions for webcam continuity, protected fitting, and viewport motion."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path

import numpy as np
import pytest

from chomp.camera_motion import camera_path
from chomp.manual import render_manual_preview
from chomp.reframe import Camera, Sample, _bridge_camera_gaps, _confirm_edge_panels, _refine_panels

from test_stage1_integration import ffmpeg, needs_ffmpeg


@pytest.mark.parametrize("removed_in_source", [False, True])
def test_bridge_requires_matching_live_pixels_at_source_cadence(monkeypatch, removed_in_source):
    pytest.importorskip("cv2")
    size = (320, 180)
    camera = Camera((250, 85, 32, 41), (222, 45, 98, 130), .85)
    sample = np.zeros((180, 320, 3), dtype=np.uint8)
    sample[:, :] = (188, 15, 32)
    sample[45:175, 222:] = (20, 85, 230)
    sample[115:165, 222:270] = (90, 210, 40)
    tracked = [camera] * 3 + [None] * 3 + [camera] * 3
    observations = [Sample(i * 250, sample.copy(), [] if row is None else [row])
                    for i, row in enumerate(tracked)]
    decoded = np.array([sample.copy() for _ in range(30)])
    if removed_in_source:
        decoded[17, 45:175, 222:] = (188, 15, 32)
    monkeypatch.setattr("chomp.reframe._read_frames", lambda *_: decoded)
    evidence = _bridge_camera_gaps(Path("unused.mp4"), observations, tracked, size, size, 0, 2250, Fraction(30))
    if removed_in_source:
        assert not evidence and tracked[3:6] == [None] * 3
    else:
        assert evidence == [{"start_ms": 750, "end_ms": 1500, "verification_frames": 30}]
        assert tracked[3:6] == [camera] * 3


def test_coherent_edge_recovers_full_portrait_container():
    pytest.importorskip("cv2")
    size = (320, 180)
    camera = Camera((251, 116, 40, 38), (220, 75, 100, 104), .8)
    frame = np.full((180, 320, 3), (59, 125, 17), dtype=np.uint8)
    frame[25:, 220:] = (202, 37, 204)
    frame[116:154, 251:291] = (81, 108, 170)
    samples = [Sample(250 * i, frame.copy(), [camera]) for i in range(6)]
    [(start, end, refined)] = _refine_panels([(0, 6, camera)], samples, size, size)
    assert (start, end) == (0, 6)
    assert refined.box == (220, 25, 100, 154)


@pytest.mark.parametrize("appears_at_boundary", [True, False])
def test_recurring_edge_camera_needs_new_face_observations(monkeypatch, appears_at_boundary):
    small = Camera((245, 82, 23, 31), (214, 59, 100, 88), .9)
    large = Camera((204, 60, 55, 67), (133, 4, 187, 160), .92)
    tracked = [small] * 5 + [large] * 5 + [small]
    samples = [Sample(i * 250, None, [item]) for i, item in enumerate(tracked)]
    groups = [(0, 5, small), (5, 10, large), (10, 11, None)]
    frames = [0, 0, 1, 1, 1] if appears_at_boundary else [0, 1, 1, 1, 0]
    monkeypatch.setattr("chomp.reframe._read_frames", lambda *_: frames)

    class Detector:
        def detect(self, frame):
            return 1, [(small if frame else large).face]

    confirmed = _confirm_edge_panels(Path("unused.mp4"), groups, tracked, samples, (320, 180),
                                     0, 2670, Fraction(30), Detector())
    assert bool(confirmed) == appears_at_boundary
    assert groups[-1][2] == (small if appears_at_boundary else None)


def test_pan_holds_small_flicker_and_tracks_sustained_displacement():
    bounds = ((0, 300), (0, 0))
    jitter = [(100 + dx, 0) for dx in (4, -5, 8, -9, 4, -6)]
    assert camera_path((100, 0), jitter, (130, 180), bounds, "pan") == [(100, 0)] * len(jitter)
    assert camera_path((100, 0), jitter, (130, 180), bounds, "hold") == [(100, 0)] * len(jitter)
    assert len(set(camera_path((100, 0), jitter, (130, 180), bounds, "track"))) > 1

    demand = [(100, 0)] * 2 + [(160, 0)] * 20
    path = camera_path((100, 0), demand, (130, 180), bounds, "pan")
    steps = [b[0] - a[0] for a, b in zip(path, path[1:])]
    assert path[2] == (100, 0) and path[-1][0] >= 153
    assert all(0 <= step <= 10 for step in steps)
    assert abs(steps[2] - steps[1]) <= 5

    settled = camera_path((40, 0), [(60, 0), (60, 0), (44, 0), (65, 0)],
                          (100, 180), bounds, "pan")
    assert settled[1][0] > 40 and settled[3] == settled[2]


@needs_ffmpeg
def test_one_slot_with_narrow_panel_and_brief_detector_gap(tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    source = tmp_path / "portrait.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=blue:size=320x180:duration=4:rate=30",
           "-f", "lavfi", "-i", "sine=duration=4",
           "-vf", "drawbox=x=220:y=32:w=100:h=148:color=red:t=fill,"
                  "drawbox=x=223:y=90:w=40:h=45:color=green:t=fill",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source))
    rgb = ffmpeg("-i", str(source), "-frames:v", "1", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1")
    image = np.frombuffer(rgb, dtype=np.uint8).reshape(180, 320, 3)
    camera = Camera((254, 81, 38, 40), (220, 32, 100, 148), .88)
    samples = [Sample(i * 250, image, [] if 5 <= i < 8 else [camera]) for i in range(16)]
    monkeypatch.setattr("chomp.reframe._sample", lambda *_: (samples, (320, 180)))
    report = render_manual_preview(source, tmp_path / "out", rights_ref="fixture", start_s=0, end_s=4,
                                   layout="shorts", camera_motion="hold")
    edit = json.loads(Path(report["edit_spec"]).read_text(encoding="utf-8"))
    [segment] = edit["segments"]
    assert segment["layout"] == "facecam-gameplay"
    assert segment["camera_fraction"] == .35 and not segment["fit_facecam"]
    assert not segment["gameplay_path"]
    [gap] = edit["facecam_detection"]["camera_continuity"]
    assert (gap["start_ms"], gap["end_ms"]) == (1250, 2000)
    assert gap["verification_frames"] >= 29
    for t in ("1", "1.5", "2.25"):
        frame = ffmpeg("-ss", t, "-i", report["preview"], "-frames:v", "1", "-pix_fmt", "rgb24",
                       "-f", "rawvideo", "pipe:1")
        rgb = np.frombuffer(frame, dtype=np.uint8).reshape(960, 540, 3)
        assert (rgb[30:300, -22:, 0] > 155).mean() > .98
        assert (rgb[380:, 60:480, 2] > 150).mean() > .98
