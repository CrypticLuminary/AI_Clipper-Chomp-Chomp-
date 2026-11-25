"""Regression: a valid VFR source must retain video through the cut end."""
import pytest
from pathlib import Path

from chomp.manual import render_manual_preview
from chomp.media import probe
from test_stage1_integration import ffmpeg, needs_ffmpeg


@needs_ffmpeg
@pytest.mark.parametrize("temporal", [False, True])
def test_vfr_video_and_audio_reach_same_source_boundary(tmp_path, temporal):
    source = tmp_path / "vfr source.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=8,select='lt(t,4)+not(mod(n,3))'",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
           "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "aac", "-fps_mode", "vfr", str(source))
    segments = [{"start_ms": 1000, "end_ms": 3000, "layout": "gameplay-only"},
                {"start_ms": 3000, "end_ms": 5000, "layout": "gameplay-only"}] if temporal else None
    result = render_manual_preview(source, tmp_path / "export", rights_ref="generated synthetic fixture",
                                   start_s=1, end_s=5, layout="gameplay-only", layout_segments_override=segments)
    metadata = probe(Path(result["preview"]))
    streams = {row["codec_type"]: row for row in metadata["streams"]}
    assert float(streams["video"]["duration"]) == pytest.approx(4, abs=.08)
    assert float(streams["audio"]["duration"]) == pytest.approx(4, abs=.04)
    assert abs(float(streams["video"]["duration"]) - float(streams["audio"]["duration"])) < .08
