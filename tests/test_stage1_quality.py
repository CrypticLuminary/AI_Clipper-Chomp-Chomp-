"""Regressions for clock, editorial evidence, and editable review revisions."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from chomp.artifacts import digest_file
from chomp.asr import LocalTranscriber
from chomp.captions import build_captions
from chomp.clipwork import approve
from chomp.diarization.core import _merge_consecutive_turns
from chomp.diarization.identity import SpeakerIdentityMap
from chomp.discovery import discover
from chomp.evaluation.benchmark import benchmark, create_event_template
from chomp.evaluation.core import read_manifest
from chomp.manual import render_manual_preview, rerender
from chomp.media import probe, rms_dbfs
from chomp.models import Observation, SpeakerTurn, TimedWord, TranscriptSegment
from chomp.pipeline import process
from chomp.signals import final_evidence
from chomp.speakers import attribute_segments
from test_stage1_integration import ffmpeg, needs_ffmpeg, vod, write_json


@needs_ffmpeg
def test_transcriber_receives_selected_track_on_the_source_clock(tmp_path):
    source = tmp_path / "two tracks.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "color=black:size=320x180:rate=12:duration=4",
        "-f", "lavfi", "-i", "anullsrc=cl=mono:r=48000:d=4",
        "-itsoffset", "1.25", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2.75",
        "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0", "-t", "4",
        "-c:v", "libx264", "-c:a", "aac", str(source),
    )

    class InputProbe:
        def transcribe(self, samples, **options):
            first_sound = np.flatnonzero(np.abs(samples) > .03)[0] / 16000
            assert 1.24 <= first_sound <= 1.27
            assert options["word_timestamps"] is True
            word = SimpleNamespace(start=first_sound, end=first_sound + .25, word=" Hi", probability=.9)
            yieldable = SimpleNamespace(start=word.start, end=word.end, text=" Hi", words=[word])
            return [yieldable], None

    recognizer = LocalTranscriber()
    recognizer.engine = InputProbe()
    assert recognizer.transcribe(source, 0, 4000, 0, words=True) == []
    [segment] = recognizer.transcribe(source, 0, 4000, 1, words=True)
    assert 1240 <= segment["words"][0]["start_ms"] <= 1270


def test_final_evidence_uses_only_in_clip_speech_and_audio():
    observations = [
        Observation("audio", "fixture", 459000, 460000, .9, "outside cut"),
        Observation("transcript", "fixture", 422730, 452370, .99, "context only"),
    ]
    speech = [TranscriptSegment(439050, 441050, "I need someone on the front line")]
    final, contained = final_evidence(observations, speech, 433100, 441700)
    assert contained == speech
    assert len(final) == 1 and final[0].start_ms == 439050 and final[0].end_ms == 441050
    assert final[0].strength < .99
    captions = build_captions(speech, 433100, 441700, 480000)
    assert "front line" in " ".join(captions.text.split())


def test_budget_reaches_late_quiet_events():
    observations = [Observation("audio", "fixture", i * 60_000, i * 60_000 + 1000, .9, "loud")
                    for i in range(50)]
    late = Observation("transcript", "fixture", 3_350_000, 3_351_000, .3, "quiet discovery")
    chosen = discover([*observations, late], [], 3_600_000, max_proposals=14)
    assert any(late in c.observations for c in chosen)
    assert len(chosen) == 14


def test_word_timing_moves_cut_caption_to_its_word_interval():
    words = (TimedWord(1000, 1300, "before"), TimedWord(2400, 2650, "important"),
             TimedWord(2670, 3000, "line"))
    segment = TranscriptSegment(1000, 3000, "before important line", words=words)
    result = build_captions([segment], 2000, 3100, 5000)
    assert result.timing == "word_estimated"
    assert "00:00:00,400 --> 00:00:01,000" in result.text
    assert "important line" in result.text and "before" not in result.text


@needs_ffmpeg
def test_grounded_judgment_preserves_earlier_setup_and_rejects_unknown_evidence(tmp_path):
    source = vod(tmp_path / "conversation.mp4", duration=32)
    transcript = write_json(tmp_path / "wordless timestamps.json", {"segments": [
        {"start_ms": 1000, "end_ms": 4500, "text": "The map says the only exit is north."},
        {"start_ms": 21000, "end_ms": 24800, "text": "That was the exit all along, underneath us."},
    ]})

    class Judge:
        def __init__(self, broken=False):
            self.broken = broken
            self.fingerprint = {"fixture": "invalid" if broken else "valid"}

        def judge(self, context):
            return {
                "verdict": "select", "categories": ["discovery"],
                "evidence": {"setup": ["s0"], "event": ["s1"], "payoff": ["absent" if self.broken else "s1"],
                             "reaction": []},
                "scores": {"category_payoff": 4, "standalone_clarity": 4, "hook_strength": 4, "distinctiveness": 4},
                "requires_visual_verification": True, "rationale": "The earlier assumption explains the reveal.",
            }

    result = process(source, tmp_path / "jobs", rights_ref="fixture",
                     transcript_path=transcript, editorial_provider=Judge())
    [clip] = result["clips"]
    assert clip["candidate"]["start_ms"] <= 1000 and clip["candidate"]["end_ms"] >= 24800
    assert clip["boundary"]["method"] == "semantic_evidence"
    invalid = process(source, tmp_path / "jobs", rights_ref="fixture",
                      transcript_path=transcript, editorial_provider=Judge(broken=True))
    assert invalid["selected_for_review"] == 0
    ledger = json.loads(Path(invalid["candidate_ledger"]).read_text(encoding="utf-8"))
    assert all(record["decision"] == "semantic_abstention" for record in ledger["candidates"])


@needs_ffmpeg
def test_temporal_render_and_caption_revision_bind_approval_to_the_export(tmp_path):
    source = vod(tmp_path / "video.mp4", duration=7, audio_delay=1.25)
    layout = write_json(tmp_path / "layout.json", {"segments": [
        {"start_ms": 500, "end_ms": 3200, "layout": "fit"},
        {"start_ms": 3200, "end_ms": 5500, "layout": "facecam-gameplay", "facecam_roi": [20, 20, 80, 70]},
    ]})
    out = tmp_path / "exports"
    base = render_manual_preview(source, out, rights_ref="fixture", start_s=.5, end_s=5.5, layout_plan=layout)
    assert base["layout"] == "temporal" and base["layout_review_required"]
    preview = Path(base["preview"])
    assert probe(preview)["duration_ms"] == pytest.approx(5000, abs=85)
    audio_start = ffmpeg("-i", str(preview), "-map", "0:a", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1")
    assert rms_dbfs(audio_start[:8000]) < -60
    assert rms_dbfs(audio_start[16000:24000]) > -40

    corrected = tmp_path / "corrected.srt"
    corrected.write_text("1\n00:00:01,000 --> 00:00:02,500\nExact correction.\n", encoding="utf-8")
    revised = rerender(Path(base["edit_spec"]), out, captions_override=corrected)
    assert revised["caption_timing"] == "editor_supplied"
    assert revised["preview"] != base["preview"] and digest_file(preview) == base["artifact_sha256"]["preview"]
    manual_review = Path(revised["review_manifest"])
    assert len(read_manifest(manual_review).clips) == 1
    approved = approve(manual_review, out / "approval.json", reviewer="editor", clip_index=1, watched=True)
    assert approved["artifact_sha256"]["preview"] == digest_file(Path(revised["preview"]))
    Path(revised["captions_srt"]).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        approve(manual_review, out / "stale approval.json", reviewer="editor", clip_index=1, watched=True)


def test_source_event_benchmark_counts_misses_and_keeps_partial_review_explicit(tmp_path):
    ledger = write_json(tmp_path / "candidates.json", {
        "schema_version": "chomp-candidates/1", "source_id": "fixture-source", "source": "vod.mp4",
        "job_id": "fixture-job", "duration_ms": 90000, "candidates": [
            {"proposal": {"start_ms": 9000, "end_ms": 23000}, "decision": "selected",
             "candidate": {"start_ms": 14000, "end_ms": 23000}},
        ],
    })
    events_path = tmp_path / "events.json"
    data = create_event_template(ledger, events_path, reviewer="editor")
    data["events"] = [
        {"id": "discovered", "core_ms": [17000, 20000], "essential_ms": [11000, 21000],
         "start_range_ms": [9000, 11000], "end_range_ms": [21000, 24000]},
        {"id": "missed", "core_ms": [49000, 52000], "essential_ms": [46000, 54000],
         "start_range_ms": [41000, 46000], "end_range_ms": [54000, 59000]},
    ]
    write_json(events_path, data)
    assert benchmark(events_path, ledger)["proposal_recall"] is None
    data["complete"] = True
    write_json(events_path, data)
    result = benchmark(events_path, ledger)
    assert result["proposal_recall"] == .5
    assert result["events"][0]["selected"] and not result["events"][0]["essential_preserved"]
    assert not result["events"][1]["discovered"]


def test_duplicate_speaker_turns_and_string_confirmation_cannot_add_certainty():
    segment = TranscriptSegment(0, 1000, "Unknown voices")
    a, b = SpeakerTurn(0, 500, "A"), SpeakerTurn(500, 1000, "B")
    assert attribute_segments([segment], [a, a, b]) == [segment]
    assert _merge_consecutive_turns([SpeakerTurn(0, 5000, "A"), SpeakerTurn(1000, 2000, "A")]) == [
        SpeakerTurn(0, 5000, "A")
    ]
    with pytest.raises(ValueError, match="true or false"):
        SpeakerIdentityMap.from_json_dict({"main_speaker_id": "A", "mappings": {"A": "Name"}, "confirmed": "false"})
