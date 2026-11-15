"""Verify framing decisions with deterministic detection samples and actual renders."""
from pathlib import Path
import json
import shutil
import subprocess
import sys

import numpy as np
import pytest

from chomp.framing import FramingPlan, _padded_roi, _pick_facecam, plan_framing
from chomp.manual import render_manual_preview
from chomp.media import exported_audio_level, probe
from chomp.pipeline import process


RIGHT_FACE = (274, 34, 24, 26)
LEFT_FACE = (19, 36, 23, 26)
FFMPEG_REQUIRED = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg and FFprobe are required",
)


def moving_patches(*boxes):
    frames = []
    for index in range(8):
        frame = np.zeros((180, 320), dtype=np.uint8)
        for x, y, w, h in boxes:
            frame[y:y + h, x:x + w] = 20 * index
        frames.append(frame)
    return frames


def test_recurring_edge_face_and_padded_roi():
    samples = [[(RIGHT_FACE[0] + index % 3, *RIGHT_FACE[1:])] if index != 3 else [] for index in range(8)]
    box, support, _ = _pick_facecam(samples, moving_patches(RIGHT_FACE), 320, 180)
    assert support == 7 and box == (275, 34, 24, 26)
    x, y, w, h = _padded_roi(box, 320, 180)
    assert 0 <= x < box[0] < box[0] + box[2] <= x + w <= 320
    assert 0 <= y < box[1] < box[1] + box[3] <= y + h <= 180
    assert all(value % 2 == 0 for value in (x, y, w, h))


def test_uncertain_or_transient_faces_use_the_full_frame():
    static = moving_patches()
    assert _pick_facecam([[RIGHT_FACE]] * 8, static, 320, 180)[0] is None
    transient = [[RIGHT_FACE]] * 5 + [[]] * 3
    assert _pick_facecam(transient, moving_patches(RIGHT_FACE), 320, 180)[0] is None
    crowded = [[RIGHT_FACE, LEFT_FACE]] * 8
    box, count, reason = _pick_facecam(crowded, moving_patches(RIGHT_FACE, LEFT_FACE), 320, 180)
    assert box is None and count == 8 and "multiple" in reason
    center_face = (125, 53, 68, 70)
    assert _pick_facecam([[center_face]] * 8, moving_patches(center_face), 320, 180)[0] is None


def test_a_camera_that_zooms_mid_clip_uses_the_full_frame():
    zoomed = (220, 18, 60, 68)
    samples = [[RIGHT_FACE]] * 3 + [[zoomed]] * 2 + [[RIGHT_FACE]] * 3
    box, count, reason = _pick_facecam(samples, moving_patches(RIGHT_FACE, zoomed), 320, 180)
    assert box is None and count == 6 and "changes" in reason


def test_manual_facecam_roi_takes_precedence(monkeypatch):
    def unexpected_sample(*_args):
        pytest.fail("explicit facecam ROI should skip detection")

    monkeypatch.setattr("chomp.framing._sample_facecam", unexpected_sample)
    plan = plan_framing(Path("unused.mp4"), 10, 2000, "auto-facecam", RIGHT_FACE)
    assert plan == FramingPlan("facecam-gameplay", RIGHT_FACE, {"method": "supplied_roi"})


def make_media(tmp_path):
    folder = tmp_path / "facecam workspace"
    folder.mkdir()
    source = folder / "local vod.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
        "color=black:size=320x180:rate=12:duration=12", "-f", "lavfi", "-i", "sine=duration=12",
        "-vf", "drawbox=x=248:y=18:w=70:h=61:color=red:t=fill,"
        "drawbox=x=0:y=90:w=20:h=30:color=lime:t=fill,"
        "drawbox=x=300:y=90:w=20:h=30:color=blue:t=fill",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    ], check=True, capture_output=True, timeout=30)
    return source


def pixel(preview, x, y):
    frame = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", "0.4", "-i", str(preview),
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-threads", "1", "-",
    ], check=True, capture_output=True, timeout=10).stdout
    assert len(frame) == 540 * 960 * 3
    i = (y * 540 + x) * 3
    return tuple(frame[i:i + 3])


@FFMPEG_REQUIRED
def test_detected_facecam_flows_into_ranked_preview_and_edit_spec(tmp_path, monkeypatch):
    source = make_media(tmp_path)
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({"segments": [{"start_ms": 4000, "end_ms": 8000, "text": "Wow!"}]}))
    sampled = []
    detected_roi = (250, 20, 62, 56)

    def sampled_facecam(path, start, end, model):
        sampled.append((path, start, end, model))
        return FramingPlan("facecam-gameplay", detected_roi, {"method": "recurring_face", "matching_frames": 8})

    monkeypatch.setattr("chomp.framing._sample_facecam", sampled_facecam)
    manifest = process(source, tmp_path / "ranked exports", rights_ref="synthetic",
                       transcript_path=transcript, layout="auto-facecam", min_score=0, render=True)
    clip, = manifest["clips"]
    spec = json.loads(Path(clip["edit_spec"]).read_text())
    assert sampled == [(source.resolve(), spec["source_start_ms"], spec["source_end_ms"], None)]
    assert spec["layout"] == "facecam-gameplay" and spec["facecam_roi_px"] == list(detected_roi)
    assert spec["facecam_detection"]["matching_frames"] == 8 and spec["human_review_required"] is True
    assert spec["fit_facecam"] is True
    preview = Path(clip["preview"])
    r, g, b = pixel(preview, 270, 100)
    assert r > 160 and g < 35 and b < 35
    assert max(pixel(preview, 10, 100)) < 30
    assert exported_audio_level(preview) > -45


@FFMPEG_REQUIRED
def test_no_opencv_falls_back_to_full_frame_and_keeps_edge_content(tmp_path, monkeypatch):
    source = make_media(tmp_path)
    monkeypatch.setitem(sys.modules, "cv2", None)
    report = render_manual_preview(source, tmp_path / "manual review", rights_ref="synthetic",
                                   start_s=1, end_s=4, layout="auto-facecam")
    assert report["layout"] == "fit" and report["facecam_roi_px"] is None
    assert report["facecam_detection"]["detector"] == "unavailable"
    assert report["publish_status"] == "disabled"
    preview = Path(report["preview"])
    streams = probe(preview)["streams"]
    assert (streams[0]["width"], streams[0]["height"], streams[0]["codec_name"]) == (540, 960, "h264")
    assert streams[1]["codec_name"] == "aac" and exported_audio_level(preview) > -45
    left = pixel(preview, 10, 500)
    right = pixel(preview, 529, 500)
    assert left[1] > 160 and right[2] > 160
