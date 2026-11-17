"""Tests for framing evidence that a detected face alone cannot provide."""
from pathlib import Path

import numpy as np
import pytest

from chomp.camera_evidence import enclosing_perimeter, perimeter_support
from chomp.cutout import classify_cutout
from chomp.media import probe, render_preview
from chomp.reframe import Camera, Sample, StreamerAnchor, plan_shorts, vision_fingerprint
from test_stage1_integration import ffmpeg, needs_ffmpeg


def test_panel_needs_repeated_support_on_independent_sides():
    frames = np.zeros((8, 90, 160, 3), dtype=np.uint8)
    box = (90, 40, 70, 50)
    frames[:, :, 90:] = 200
    assert perimeter_support(frames, box) == (1., None, 0., None)
    assert not enclosing_perimeter(perimeter_support(frames, box))

    frames[0, :40, 90:] = 0
    assert not enclosing_perimeter(perimeter_support(frames, box))

    frames[:, :40, 90:] = 0
    assert enclosing_perimeter(perimeter_support(frames, box))


def test_crop_is_source_preserving_when_panel_geometry_is_unverified(monkeypatch):
    pytest.importorskip("cv2")
    video = {"codec_type": "video", "width": 320, "height": 180, "avg_frame_rate": "30/1"}
    monkeypatch.setattr("chomp.reframe.probe", lambda _: {"streams": [video]})
    scene = np.zeros((180, 320, 3), dtype=np.uint8)
    scene[:, 140:] = 215
    candidate = Camera((202, 62, 32, 48), (140, 0, 180, 180), .91)
    observations = [Sample(250*i, scene, [] if 6 <= i < 10 else [candidate]) for i in range(16)]
    monkeypatch.setattr("chomp.reframe._sample", lambda *_: (observations, (320, 180)))
    plan = plan_shorts(Path("unused.mp4"), 0, 4000, face_model=None, saliency_model=None, camera_slot="top")
    assert plan.facecam_roi is None
    assert plan.detection["source_geometry"] == "unknown"
    assert plan.detection["camera_containers"] == []
    assert plan.detection["rejected_panel_groups"] == 2
    [segment] = plan.segments
    assert (segment.start_ms, segment.end_ms, segment.layout) == (0, 4000, "letterbox")
    assert segment.facecam_roi is segment.gameplay_roi is None


@pytest.mark.parametrize("moving,alternatives,confirmed", [(True, False, True), (False, False, False),
                                                           (True, True, False)])
def test_cutout_needs_one_anchored_mask_track_with_independent_motion(monkeypatch, moving, alternatives, confirmed):
    width, height = 148, 90
    yy, xx = np.indices((height, width))
    anchors = [(19, 43, 21, 24)]
    if alternatives:
        anchors.append((108, 43, 21, 24))
    masks = {face: (abs(xx - (face[0] + face[2] / 2)) < 12) & (yy >= face[1] - 2) for face in anchors}
    monkeypatch.setattr("chomp.cutout.PersonSegmenter", lambda _: lambda _frame, face: masks[face])
    frames = []
    for i in range(12):
        shift = i * 6 if moving else 0
        pixels = 45 + 155 * (((xx + shift) // 6 + yy // 5) % 2)
        image = np.repeat(pixels[:, :, None], 3, axis=2).astype(np.uint8)
        for mask in masks.values():
            image[mask] = 90
        faces = tuple(reversed(anchors)) if i % 2 else tuple(anchors)
        frames.append(Sample(i * 250, image, [], faces))
    evidence = classify_cutout(frames, Path("optional.onnx"), (width, height))
    assert (evidence is not None) == confirmed
    if evidence:
        assert evidence["face_anchor"] == anchors[0]
        assert evidence["continuous_mask_pairs"] == evidence["independent_background_pairs"] == 11


def test_framing_cache_revision_and_model_bytes_affect_fingerprint(tmp_path):
    model = tmp_path / "local-pphumanseg.onnx"
    model.write_bytes(b"model one")
    without = vision_fingerprint(None, None)
    with_model = vision_fingerprint(None, None, person_model=model)
    model.write_bytes(b"model two")
    assert without["planner_revision"] > 11 and without["person_model_sha256"] is None
    assert with_model["person_model_sha256"] != vision_fingerprint(None, None, person_model=model)["person_model_sha256"]


@pytest.mark.parametrize("case", ["competing", "anchored", "disappears", "mixed"])
def test_reaction_framing_respects_only_the_anchored_streamer(monkeypatch, case):
    pytest.importorskip("cv2")
    video = {"codec_type": "video", "width": 320, "height": 180, "avg_frame_rate": "30/1"}
    monkeypatch.setattr("chomp.reframe.probe", lambda _: {"streams": [video]})
    host = Camera((231, 86, 32, 40), (206, 60, 114, 120), .76)
    watched = Camera((71, 37, 50, 61), (0, 0, 180, 132), .98)
    scene = np.full((180, 320, 3), 30, dtype=np.uint8)
    scene[60:, 206:] = 230
    scene[:132, :180] = 160
    host_track = (host.face, (277, 98, 33, 43)) if case == "mixed" else (host.face, watched.face)
    samples = [Sample(i * 250, scene, [watched, host], host_track) for i in range(16)]
    if case == "disappears":
        samples[12:] = [Sample(i * 250, scene, [watched], (watched.face,)) for i in range(12, 16)]
    monkeypatch.setattr("chomp.reframe._sample", lambda *_: (samples, (320, 180)))
    anchor = None if case == "competing" else StreamerAnchor(500, host.face)
    plan = plan_shorts(Path("unused.mp4"), 0, 4000, face_model=None, saliency_model=None, camera_slot="top",
                       streamer_anchor=anchor)
    if case == "anchored":
        assert plan.layout == "facecam-gameplay"
        assert plan.detection["streamer_anchor"]["face_samples"] == 16
        assert plan.detection["camera_containers"][0]["source_box"] == host.box
    else:
        assert plan.layout == "letterbox" and len(plan.segments) == 1
        assert plan.detection["camera_containers"] == []
        assert plan.detection["competing_face_tracks"] == (case == "competing")
        assert plan.detection["another_face_inside_anchored_panel"] == (case == "mixed")


@needs_ffmpeg
def test_full_source_fallback_keeps_the_temporal_frame_clock_at_a_fractional_seek(tmp_path):
    source = tmp_path / "source.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=60:duration=3",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
           "-c:v", "libx264", "-c:a", "aac", str(source))
    output = tmp_path / "complete.mp4"
    render_preview(source, output, 470, 2070, 180, 320, layout="letterbox", shorts=True)
    video, audio = probe(output)["streams"]
    assert video["nb_frames"] == "96"
    assert float(video["start_time"]) == float(audio["start_time"]) == 0.
    assert float(video["duration"]) == float(audio["duration"]) == 1.6
