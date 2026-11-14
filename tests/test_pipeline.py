import json
from pathlib import Path
import shutil
import subprocess

import pytest

from chomp.media import probe
from chomp.pipeline import process


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg not installed")
def test_end_to_end_authorized_fixture(tmp_path: Path):
    source=tmp_path/"synth.mp4"
    subprocess.run(["ffmpeg","-hide_banner","-nostdin","-y","-v","error","-f","lavfi","-i","testsrc2=size=320x180:rate=12:duration=12","-f","lavfi","-i","sine=frequency=800:duration=12","-shortest","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(source)], check=True)
    transcript=tmp_path/"words.json"
    transcript.write_text(json.dumps({"segments":[{"start":1,"end":5,"text":"This is a calm but interesting explanation."},{"start":6,"end":9,"text":"Wait what happened here?"}]}))
    out=process(source,tmp_path/"output", rights_ref="fixture-own",transcript_path=transcript,render=True,min_score=0,limit=3)
    assert out["transcript_status"]=="available" and out["chat_status"]=="missing"
    assert 1 <= out["selected_for_review"] <= 3
    assert out["auto_publishing"] is False
    for item in out["clips"]:
        assert Path(item["preview"]).is_file()
        assert probe(Path(item["preview"]))["duration_ms"]>0
    manifest=tmp_path/"output"/out["job_id"]/"review_queue.json"
    assert manifest.is_file() and json.loads(manifest.read_text())["manual_review_required"] is True


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg not installed")
def test_captioned_split_export_and_edit_spec(tmp_path: Path):
    source = tmp_path / "demo.mp4"
    subprocess.run(["ffmpeg","-hide_banner","-nostdin","-y","-v","error","-f","lavfi","-i","testsrc2=size=320x180:rate=12:duration=12","-f","lavfi","-i","sine=frequency=440:duration=12","-shortest","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(source)],check=True)
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({"segments":[{"start":1,"end":5,"text":"This is a useful first clip."},{"start":6,"end":10,"text":"And this is the payoff!"}]}))
    result = process(source,tmp_path/"exports",rights_ref="test-rights",transcript_path=transcript,min_score=0,render=True,facecam_roi=(200,0,110,80))
    assert result["clips"]
    for item in result["clips"]:
        assert item["caption_cues"] > 0
        assert Path(item["captions_srt"]).exists()
        assert item["boundary"]["method"].startswith("transcript_assisted")
        spec = json.loads(Path(item["edit_spec"]).read_text())
        assert spec["layout"] == "fixed_facecam_split"
        assert spec["facecam_roi_px"] == [200,0,110,80]
        info = probe(Path(item["preview"]))
        v=next(s for s in info["streams"] if s["codec_type"]=="video")
        assert (v["width"],v["height"],v["codec_name"]) == (540,960,"h264")
