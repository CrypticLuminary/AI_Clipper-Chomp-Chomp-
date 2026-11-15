"""Exercise the real Stage 1 handoffs, with only the diarization model stubbed."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from chomp import diarization
from chomp.boundaries import refine_boundaries
from chomp.diarization import core
from chomp.diarization.__main__ import main as diarization_cli
from chomp.evaluation import ClipLedgerError, compare, make_template, read_annotations, read_manifest
from chomp.media import exported_audio_level, probe, rms_dbfs, scan_audio
from chomp.models import Candidate, Observation, SpeakerTurn
from chomp.pipeline import process
from chomp.ranking import heuristic_rank, select
from chomp.speakers import read_speaker_turns


needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg and FFprobe are required for the Stage 1 media fixtures",
)


def ffmpeg(*args: str) -> bytes:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", *args],
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout


def vod(path: Path, *, duration: int, audio_delay: float = 0) -> Path:
    ffmpeg(
        "-f", "lavfi", "-i", f"color=black:size=320x180:rate=12:duration={duration}",
        "-itsoffset", str(audio_delay), "-f", "lavfi", "-i",
        f"sine=frequency=440:sample_rate=48000:duration={duration - audio_delay}",
        "-map", "0:v:0", "-map", "1:a:0", "-t", str(duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    )
    return path


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def run_module(module: str, *args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", module, *map(str, args)],
        capture_output=True, encoding="utf-8", timeout=60,
    )


def run_json(module: str, *args: str | Path) -> dict:
    result = run_module(module, *args)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def srt_ranges(text: str) -> list[tuple[int, int]]:
    def to_ms(timestamp: str) -> int:
        hour, minute, second, subsecond = (int(n) for n in re.split("[:,]", timestamp))
        return (hour * 3_600 + minute * 60 + second) * 1_000 + subsecond

    ranges = []
    for sequence, block in enumerate(text.strip().split("\n\n"), start=1):
        ordinal, timecode, *lines = block.splitlines()
        assert ordinal == str(sequence) and 1 <= len(lines) <= 2
        assert re.fullmatch(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", timecode)
        start, end = timecode.split(" --> ")
        ranges.append((to_ms(start), to_ms(end)))
    return ranges


@needs_ffmpeg
def test_diarization_and_transcript_reach_render_and_review(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "workspace with spaces"
    folder.mkdir()
    source = vod(folder / "source with spaces.mp4", duration=12)
    text = write_json(folder / "words with spaces.json", {"segments": [
        {"start_ms": 1500, "end_ms": 3800, "text": "नमस्ते साथी!"},
        {"start_ms": 4300, "end_ms": 6200, "text": "Look at this!"},
        {"start_ms": 9000, "end_ms": 11000, "text": "That was close!"},
    ]})
    seg_model = folder / "fake segmentation.onnx"
    emb_model = folder / "fake embedding.onnx"
    seg_model.touch()
    emb_model.touch()
    turns = [SpeakerTurn(1000, 4000, "SPEAKER_00"), SpeakerTurn(4200, 11200, "SPEAKER_01")]

    def model_stub(wav_path, duration_ms, config):
        assert wav_path.is_file() and duration_ms == 12000
        assert (config.segmentation_model, config.embedding_model) == (seg_model, emb_model)
        return diarization.DiarizationResult(turns, 2, "synthetic-fixture")

    monkeypatch.setattr(core, "sherpa_onnx", object())
    monkeypatch.setattr(core, "_diarize_sherpa", model_stub)
    voice_args = ["--media", str(source), "--output", str(folder / "speaker artifacts"), "--backend", "sherpa",
                  "--segmentation-model", str(seg_model), "--embedding-model", str(emb_model)]
    assert diarization_cli(voice_args) == 0
    sidecars = json.loads(capsys.readouterr().out)
    turns_path = Path(sidecars["speaker_turns"])
    identity_path = Path(sidecars["identity_map"])
    assert read_speaker_turns(turns_path, 12000) == turns
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    assert identity["confirmed"] is False and identity["main_speaker_id"] is None
    identity.update(confirmed=True, main_speaker_id="SPEAKER_00", confirmation_method="fixture-manual-review")
    write_json(identity_path, identity)
    before = (turns_path.read_bytes(), identity_path.read_bytes())
    assert diarization_cli(voice_args) == 1
    assert "already exists" in capsys.readouterr().err
    assert (turns_path.read_bytes(), identity_path.read_bytes()) == before

    result = run_json(
        "chomp", "--input", source, "--output", folder / "review exports", "--rights-ref", "synthetic-fixture",
        "--acknowledge-permission", "--transcript", text, "--speaker-turns", turns_path, "--min-score", "0", "--render",
    )
    queue = Path(result["review_queue"])
    manifest = json.loads(queue.read_text(encoding="utf-8"))
    assert manifest["speaker_attribution_status"] == "turns_imported" and manifest["main_speaker_id"] is None
    assert manifest["speaker_ids"] == ["SPEAKER_00", "SPEAKER_01"]
    assert manifest["manual_review_required"] is True and manifest["auto_publishing"] is False
    clip, = manifest["clips"]
    assert clip["boundary"]["method"].startswith("transcript_assisted")
    candidate = clip["candidate"]
    start, end, anchor = (candidate[key] for key in ("start_ms", "end_ms", "anchor_ms"))
    assert all(type(value) is int for value in (start, end, anchor))
    assert (clip["boundary"]["start_ms"], clip["boundary"]["end_ms"]) == (start, end)
    assert 0 <= start <= anchor <= end <= 12000
    assert {entry["role"] for entry in candidate["speaker_context"]} == {"unverified"}
    assert {entry["speaker_id"] for entry in candidate["speaker_context"]} == set(manifest["speaker_ids"])
    assert clip["review_status"] == "pending" and clip["publish_status"] == "disabled"

    subtitles = Path(clip["captions_srt"]).read_text(encoding="utf-8")
    assert "नमस्ते साथी!" in subtitles
    cues = srt_ranges(subtitles)
    assert len(cues) == clip["caption_cues"]
    assert all(0 <= first < last <= end - start for first, last in cues)
    assert all(a[1] <= b[0] for a, b in zip(cues, cues[1:]))
    assert not any(first <= 7600 - start < last for first, last in cues)
    assert json.loads(Path(clip["edit_spec"]).read_text())["caption_timing"] == "segment_estimated_not_word_aligned"
    preview = Path(clip["preview"])
    meta = probe(preview)
    assert abs(meta["duration_ms"] - (end - start)) < 150
    video, audio = meta["streams"]
    assert (video["codec_name"], video["width"], video["height"], audio["codec_name"]) == ("h264", 540, 960, "aac")
    assert exported_audio_level(preview) > -45

    def brightest_at(source_ms):
        data = ffmpeg("-ss", str((source_ms - start) / 1000), "-i", str(preview), "-frames:v", "1",
                      "-f", "rawvideo", "-pix_fmt", "gray", "-threads", "1", "-")
        assert len(data) == 540 * 960
        return max(data)

    # White ASCII letters on the fixture's black background prove FFmpeg actually burned captions.
    assert brightest_at(5300) > 170 and brightest_at(7600) < 30
    ledger = folder / "human review.json"
    assert run_json("chomp.evaluation", "template", queue, "--out", ledger)["created"] is True
    [entry] = read_annotations(read_manifest(queue), ledger)["clips"]
    assert (entry["start_ms"], entry["end_ms"], entry["anchor_ms"], entry["decision"]) == (start, end, anchor, None)


@needs_ffmpeg
def test_diarization_round_trip_uses_source_clock_with_delayed_audio(tmp_path, monkeypatch):
    source = vod(tmp_path / "delayed audio.mp4", duration=4, audio_delay=1.25)
    monkeypatch.setattr(core, "sherpa_onnx", object())

    def diarize_fixture(wav_path, duration_ms, _config):
        with wave.open(str(wav_path), "rb") as audio:
            assert audio.getframerate() == 16000
            assert rms_dbfs(audio.readframes(8000)) < -80
            audio.setpos(2 * audio.getframerate())
            assert rms_dbfs(audio.readframes(8000)) > -40
        return diarization.DiarizationResult([SpeakerTurn(2000, duration_ms, "SPEAKER_00")], 1, "fixture")

    monkeypatch.setattr("chomp.diarization.integration.diarize_audio", diarize_fixture)
    _, turns, _ = diarization.diarize_and_export(source, tmp_path / "speaker data")
    assert read_speaker_turns(turns, probe(source)["duration_ms"]) == [SpeakerTurn(2000, 4000, "SPEAKER_00")]
    assert scan_audio(source)[0] < -80


@needs_ffmpeg
def test_audio_only_boundaries_use_the_selected_audio_track(tmp_path):
    source = tmp_path / "two audio tracks.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "color=black:size=320x180:rate=12:duration=28",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=48000:duration=28",
        "-f", "lavfi", "-i", "aevalsrc='0.3*sin(2*PI*440*t)*between(t,10,12)':s=48000:d=28",
        "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    )
    out = tmp_path / "review"
    silent = process(source, out, rights_ref="fixture", audio_stream=0, min_score=0)
    active = process(source, out, rights_ref="fixture", audio_stream=1, min_score=0)
    assert silent["clips"] == []
    [clip] = active["clips"]
    assert clip["boundary"]["method"] == "signal_padding_audio_silence"
    assert 10000 <= clip["candidate"]["anchor_ms"] <= 12500
    assert clip["captions_srt"] is None and clip["caption_cues"] == 0


@needs_ffmpeg
def test_review_denominators_and_source_clock_comparison(tmp_path):
    source = vod(tmp_path / "four moments.mp4", duration=120)
    speech = ("Did you see the setup for this turn? Can you see how the result follows from it? "
              "The group checked the map and planned what to do before the countdown. "
              "Then the player changed course when the gate opened unexpectedly! That was the surprise.")
    transcript = write_json(tmp_path / "separate moments.json", {"segments": [
        {"start_ms": sec * 1000, "end_ms": (sec + 8) * 1000, "text": speech} for sec in (7, 37, 67, 97)
    ]})
    root = tmp_path / "integration review"
    first = process(source, root, rights_ref="fixture", transcript_path=transcript)
    assert first["candidate_count"] == 4 and first["selected_for_review"] == 3
    manifest_path = root / first["job_id"] / "review_queue.json"
    manifest = read_manifest(manifest_path)
    labels_path = tmp_path / "fixture labels.json"
    assert make_template(manifest, labels_path) is True
    labels = read_annotations(manifest, labels_path)
    # Test data exercising rubric denominators. No actual VOD judgment is written.
    labels["clips"][0].update(decision="good", reviewer_id="fixture-editor")
    labels["clips"][1].update(decision="uncertain", reviewer_id="fixture-editor")
    write_json(labels_path, labels)
    label_bytes = labels_path.read_bytes()
    assert make_template(manifest, labels_path) is False and labels_path.read_bytes() == label_bytes
    report = run_json("chomp.evaluation", "report", "--manifest", manifest_path, "--reviews", labels_path)
    assert report["review_coverage"] == {
        "reviewed": 2, "unreviewed": 1, "uncertain": 1, "determinate": 1,
        "reviewed_rate": .666667, "determinate_rate": .333333,
    }
    assert report["precision_at_3"]["value"] is None
    assert report["interesting_rate"]["denominator"] == 1

    second = process(source, root, rights_ref="fixture", transcript_path=transcript, layout="letterbox")
    other_manifest = read_manifest(root / second["job_id"] / "review_queue.json")
    new_labels = tmp_path / "new fixture labels.json"
    make_template(other_manifest, new_labels)
    delta = compare(manifest, labels, other_manifest, read_annotations(other_manifest, new_labels))
    assert first["job_id"] != second["job_id"] and len(delta["matched"]) == 3
    assert delta["transitions_determinate_matched"] == {} and delta["metric_deltas"]["precision_at_3"] is None
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["duration_ms"] += 2000
    mismatched = read_manifest(write_json(tmp_path / "different clock.json", data))
    with pytest.raises(ClipLedgerError, match="clocks may not align"):
        compare(manifest, labels, mismatched, labels)
    assert labels_path.read_bytes() == label_bytes


@pytest.mark.parametrize("version", ["0.2", "0.3"])
def test_legacy_queue_can_be_compared_without_migrating_it(tmp_path, version):
    shared = {
        "source": "same authorized source.mp4", "duration_ms": 60_000,
        "candidate_count": 1, "selected_for_review": 1,
        "clips": [{"candidate": {"start_ms": 5000, "end_ms": 19000, "anchor_ms": 9000}, "preview": None}],
    }
    old_path = write_json(tmp_path / "old queue.json", {**shared, "schema_version": version, "job_id": "old"})
    old_bytes = old_path.read_bytes()
    new_path = write_json(tmp_path / "new queue.json", {**shared, "schema_version": "0.4", "job_id": "new"})
    old, new = read_manifest(old_path), read_manifest(new_path)
    old_review, new_review = tmp_path / "older review.json", tmp_path / "newer review.json"
    make_template(old, old_review)
    make_template(new, new_review)
    comparison = compare(old, read_annotations(old, old_review), new, read_annotations(new, new_review))
    assert len(comparison["matched"]) == 1
    assert comparison["matched"][0]["old_decision"] is None
    assert comparison["matched"][0]["anchor_distance_ms"] == 0
    assert old_path.read_bytes() == old_bytes


@needs_ffmpeg
@pytest.mark.parametrize("provide_transcript", [False, True], ids=["no-transcript-or-chat", "zero-clip-budget"])
def test_empty_selection_is_valid_for_clipledger(tmp_path, provide_transcript):
    source = vod(tmp_path / "empty selection.mp4", duration=8)
    options = {}
    if provide_transcript:
        options = {"limit": 0, "min_score": 0, "transcript_path": write_json(tmp_path / "transcript.json", {
            "segments": [{"start_ms": 1000, "end_ms": 7000, "text": "Fixture speech."}],
        })}
    root = tmp_path / "review"
    manifest = process(source, root, rights_ref="fixture", **options)
    assert manifest["chat_status"] == "missing"
    assert manifest["transcript_status"] == ("available" if provide_transcript else "missing")
    assert manifest["speaker_attribution_status"] == "unavailable"
    assert manifest["clips"] == [] and manifest["selected_for_review"] == 0
    if provide_transcript:
        assert manifest["candidate_count"] > 0
    queue = root / manifest["job_id"] / "review_queue.json"
    assert not list(queue.parent.glob("candidate_*"))
    labels = tmp_path / "empty review.json"
    run_json("chomp.evaluation", "template", queue, "--out", labels)
    report = run_json("chomp.evaluation", "report", "--manifest", queue, "--reviews", labels)
    assert report["selected_count"] == 0 and report["precision_at_3"]["value"] is None


def test_refined_audio_candidates_rank_before_duplicate_suppression():
    duplicate = Candidate(5000, 23000, 11000, [Observation("audio", "fixture", 10000, 12000, .4, "quieter")])
    winner = Candidate(8000, 26000, 14000, [Observation("audio", "fixture", 13000, 15000, .9, "louder")])
    for candidate in [duplicate, winner]:
        before = candidate.anchor_ms
        decision = refine_boundaries(candidate, [], 50000)
        candidate.validate(50000)
        assert candidate.anchor_ms == before and decision.method == "signal_padding_fallback"
        heuristic_rank(candidate)
    assert select([duplicate, winner], min_score=0) == [winner]
    assert select([duplicate, winner], min_score=45) == []
    duplicate.score = float("nan")
    with pytest.raises(ValueError, match="score"):
        select([duplicate, winner], min_score=0)


def test_base_cli_with_model_dependencies_and_numpy_unavailable(tmp_path):
    script = '''
import runpy, sys
for name in ('numpy', 'sherpa_onnx', 'resemblyzer', 'sklearn', 'faster_whisper'):
    sys.modules[name] = None
module = sys.argv.pop(1)
runpy.run_module(module, run_name='__main__', alter_sys=True)
'''
    for module in ("chomp", "chomp.evaluation", "chomp.diarization"):
        help_result = subprocess.run([sys.executable, "-c", script, module, "--help"], capture_output=True, text=True)
        assert help_result.returncode == 0, help_result.stderr
        assert "usage:" in help_result.stdout
    missing = subprocess.run([
        sys.executable, "-c", script, "chomp.diarization", "--media", str(tmp_path / "not read.mp4"),
    ], capture_output=True, text=True)
    assert missing.returncode == 1 and "No usable diarization backend" in missing.stderr
    assert "numpy" in missing.stderr.lower()
