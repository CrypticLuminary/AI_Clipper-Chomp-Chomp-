"""Audio cue timing and source-audio preservation through the public clip workflow."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from chomp.cli import main
from chomp.manual import rerender

from test_stage1_integration import ffmpeg, needs_ffmpeg, vod, write_json


def _frequency_level(path: Path, frequency: int, start: float, end: float) -> float:
    raw = ffmpeg("-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1")
    samples = np.frombuffer(raw, dtype="<f4")[round(start * 8000):round(end * 8000)]
    angles = 2 * np.pi * frequency * np.arange(len(samples)) / 8000
    return float(2 * abs(np.dot(samples, np.exp(1j * angles))) / len(samples))


@needs_ffmpeg
def test_timed_cue_preserves_original_track_and_follows_source_clock_on_revision(tmp_path, capsys):
    original = vod(tmp_path / "vod.mp4", duration=5, audio_delay=.5)
    cue = tmp_path / "cue.wav"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=1200:sample_rate=48000:duration=1",
           "-af", "volume=5", "-c:a", "pcm_s16le", str(cue))
    schedule = write_json(tmp_path / "sound.json", {"cues": [
        {"path": "cue.wav", "at_ms": 2350, "duration_ms": 300, "offset_ms": 100, "gain_db": -9},
    ]})
    out = tmp_path / "renders"
    assert main(["--input", str(original), "--output", str(out), "--rights-ref", "test",
                 "--acknowledge-permission", "--start", "1", "--end", "4", "--layout", "gameplay-only",
                 "--sound-cues", str(schedule), "--render"]) == 0
    review = json.loads(capsys.readouterr().out)
    preview = Path(review["preview"])
    assert _frequency_level(preview, 1200, 1.40, 1.60) > .08
    assert _frequency_level(preview, 1200, 1.10, 1.30) < .005
    assert _frequency_level(preview, 1200, 1.70, 1.90) < .005
    assert _frequency_level(preview, 440, 1.40, 1.60) > .08
    assert review["review_status"] == "pending" and review["publish_status"] == "disabled"

    revision = rerender(Path(review["edit_spec"]), out, start_s=2.5, end_s=4)
    cropped = Path(revision["preview"])
    assert _frequency_level(cropped, 1200, .03, .12) > .08
    assert _frequency_level(cropped, 1200, .20, .30) < .005
    assert _frequency_level(cropped, 440, .03, .12) > .08
    assert json.loads(Path(revision["edit_spec"]).read_text())["sound_cues"][0]["at_ms"] == 2350

    cue.write_bytes(cue.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="asset changed"):
        rerender(Path(review["edit_spec"]), out)
