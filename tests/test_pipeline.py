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


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg not installed")
def test_export_has_decodable_non_silent_audio_and_short_captions(tmp_path: Path):
    from chomp.media import exported_audio_level, render_preview
    from chomp.captions import _chunks
    source=tmp_path/"short.mp4"
    subprocess.run(["ffmpeg","-hide_banner","-nostdin","-y","-v","error",
      "-f","lavfi","-i","testsrc2=size=320x180:rate=12:duration=6",
      "-f","lavfi","-i","sine=frequency=600:duration=6",
      "-shortest","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(source)],check=True)
    out=tmp_path/"candidate_01.mp4"
    srt=tmp_path/"candidate_01.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:05,000\nWait, what just happened?\n", encoding="utf8")
    render_preview(source,out,0,5000,captions_path=srt)
    assert exported_audio_level(out) > -45
    assert any(s["codec_type"]=="audio" for s in probe(out)["streams"])
    assert all(len(chunk)<=28 for chunk in _chunks("Healing look at that. Look at that. I will play what the team needs."))


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg not installed")
def test_pipeline_preserves_main_streamer_attribution(tmp_path: Path):
    source=tmp_path/"speaker.mp4"
    subprocess.run(["ffmpeg","-hide_banner","-nostdin","-y","-v","error", "-f","lavfi","-i","testsrc2=size=320x180:rate=12:duration=12", "-f","lavfi","-i","sine=frequency=600:duration=12", "-shortest","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(source)],check=True)
    transcript=tmp_path/"labelled.json"
    transcript.write_text(json.dumps({"segments":[{"start":1,"end":5,"text":"Streamer's speech", "speaker_id":"S0"}, {"start":6,"end":10,"text":"Another person's speech","speaker_id":"S1"}]}))
    outcome=process(source,tmp_path/"review", rights_ref="fixture",transcript_path=transcript,min_score=0,main_speaker_id="S0")
    assert outcome["main_speaker_id"]=="S0"
    assert outcome["speaker_attribution_status"]=="transcript_labelled"
    roles={s["role"] for c in outcome["clips"] for s in c["candidate"]["speaker_context"]}
    assert "main_streamer" in roles and "other_speaker" in roles
    with pytest.raises(ValueError,match="does not occur"):
        process(source,tmp_path/"review",rights_ref="fixture",transcript_path=transcript,min_score=0,main_speaker_id="S9")


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg not installed")
def test_render_with_spaces_in_paths_and_caption_file(tmp_path: Path):
    """Regression for Windows paths with spaces and FFmpeg subtitles."""
    from chomp.media import exported_audio_level, render_preview
    folder = tmp_path / "workspace with spaces" / "export directory"
    folder.mkdir(parents=True)
    source = folder / "input with spaces.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=12:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=700:duration=3",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    ], check=True)
    subtitles = folder / "candidate_01.srt"
    subtitles.write_text("1\n00:00:00,200 --> 00:00:02,500\nHello, world!\n", encoding="utf-8")
    output = folder / "candidate_01.mp4"
    render_preview(source, output, 0, 2800, captions_path=subtitles)
    meta = probe(output)
    assert any(stream.get("codec_type") == "video" and stream.get("codec_name") == "h264" for stream in meta["streams"])
    assert any(stream.get("codec_type") == "audio" and stream.get("codec_name") == "aac" for stream in meta["streams"])
    assert exported_audio_level(output) > -45
