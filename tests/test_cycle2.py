"""Cycle 2 source-clock, benchmark, provider and deterministic-render regressions."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from chomp.artifacts import artifact_key, digest_file, source_identity, write_json
from chomp.director.campaign import resolve_campaign
from chomp.director.clip import run_clip
from chomp.director.production import export_selection, verify_selection
from chomp.director.provider import Response
from chomp.director.semantic import BudgetPolicy
from chomp.editorial_selection import adapt_director_report
from chomp.evaluation.benchmark import benchmark, validate_dataset, validate_events
from chomp.evaluation.core import read_manifest
from chomp.evaluation.technical import inspect_export
from chomp.research_interfaces import collect_candidates, evidence_item, semantic_audio, validate_evidence
from chomp.research_interfaces import retrieve_frames, RetrievalBudget, sample_times
from test_stage1_integration import ffmpeg, needs_ffmpeg, vod


class ScriptedDirector:
    fingerprint = {"provider": "fixture", "model": "cycle2-script-v1"}

    def complete_structured(self, prompt, contract, *, timeout):
        payload = json.loads(prompt.split("Evidence and campaign JSON\n")[1])
        if "window" in payload:
            return Response("response_received", decision={"proposals": [
                {"event": "Scripted explanation", "viewer_value": "Synthetic pipeline verification", "evidence_ids": ["u1", "u2"]}
            ], "leads": []})
        cid = payload["candidates"][0]["candidate_id"]
        return Response("response_received", decision={"requests": [], "decisions": [{
            "candidate_id": cid, "verdict": "select", "event": "Scripted explanation",
            "viewer_value": "Synthetic pipeline verification", "reason": "Scripted setup and payoff are both supplied.",
            "audience": "Test reviewers", "campaign_fit": "A synthetic fixture", "confidence": "medium",
            "evidence_ids": ["u1", "u2"], "setup_ids": ["u1"], "development_ids": [], "payoff_ids": ["u2"],
            "title_suggestion": "Synthetic fixture", "hook_suggestion": None,
        }]})


def fixture_transcript(path):
    write_json(path, {"segments": [
        {"start_ms": 1000, "end_ms": 2500, "text": "This is the scripted setup.", "speaker_id": "A"},
        {"start_ms": 3000, "end_ms": 5000, "text": "यो परीक्षणको अन्तिम भाग हो।", "speaker_id": "B"},
    ]})
    return path


def fixture_campaign():
    return resolve_campaign(rights_ref="generated synthetic fixture", min_duration=1, duration=4, max_duration=8)


@pytest.fixture
def director_report(tmp_path, monkeypatch):
    source = tmp_path / "fake.mp4"
    source.write_bytes(b"fixture stub")
    monkeypatch.setattr("chomp.director.clip.probe", lambda p: {"duration_ms": 8000})
    monkeypatch.setattr("chomp.director.clip.check_audio_track", lambda *a: None)
    monkeypatch.setattr("chomp.director.clip._audio_curve", lambda *a, **kw: ([], False))
    transcript = fixture_transcript(tmp_path / "transcript.json")
    return run_clip(source, tmp_path / "jobs", fixture_campaign(), provider=ScriptedDirector(), transcript_path=transcript)


def test_director_adapter_preserves_decisions_provenance_and_null_scores(director_report):
    report = director_report
    adapted = adapt_director_report(report)
    assert adapted["source_id"] == report["source_id"]
    [row] = adapted["candidates"]
    assert row["candidate"]["score"] is None
    assert [row["candidate"][k] for k in ("start_ms", "end_ms")] == [1000, 5000]
    assert row["editorial"]["setup_ids"] == ["u1"]
    assert adapted["director_report"] == report
    assert adapted["timing"]["remote_api_cost"] is None
    rejected = deepcopy(report)
    rejected["decisions"][0]["verdict"] = "reject"
    rejected["selected_count"] = 0
    assert adapt_director_report(rejected)["candidates"][0]["decision"] == "reject"
    rejected["decisions"][0]["verdict"] = "abstain"
    assert adapt_director_report(rejected)["candidates"][0]["decision"] == "abstain"


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(selected_count=True),
    lambda r: r.update(duration_ms=0),
    lambda r: r["candidates"].append(r["candidates"][0]),
    lambda r: r["decisions"].append(r["decisions"][0]),
    lambda r: r["decisions"][0].update(evidence_ids=["missing"]),
    lambda r: r["decisions"][0]["boundary"].update(start_ms=1.5),
    lambda r: r["decisions"][0]["evidence"][0].update(source_id="other"),
    lambda r: r["decisions"][0].update(payoff_ids=["absent"]),
    lambda r: r["decisions"][0].update(verdict="approved"),
    lambda r: r.update(elapsed_seconds=float("nan")),
])
def test_adapter_rejects_malformed_reports(director_report, mutation):
    report = deepcopy(director_report)
    mutation(report)
    with pytest.raises((ValueError, TypeError)):
        adapt_director_report(report)


def test_benchmark_director_partial_annotations_and_source_mismatch(tmp_path, director_report):
    events = {"schema_version": "chomp-source-events/1", "source_id": director_report["source_id"],
              "duration_ms": 8000, "reviewer": "synthetic fixture", "complete": False,
              "events": [{"id": "e1", "core_ms": [3000, 4000], "essential_ms": [1000, 5000],
                          "start_range_ms": [0, 1000], "end_range_ms": [5000, 6000]}]}
    path = tmp_path / "events.json"
    write_json(path, events)
    report_path = Path(director_report["job_report"])
    result = benchmark(path, report_path)
    assert result["proposal_recall"] is None
    assert result["metrics"]["proposal_recall"]["numerator"] == 1
    assert result["metrics"]["human_approved_clip_precision"]["value"] is None
    events["complete"] = True
    write_json(path, events)
    assert benchmark(path, report_path)["proposal_recall"] == 1
    events["source_id"] = "different-source"
    write_json(path, events)
    with pytest.raises(ValueError, match="same source"):
        benchmark(path, report_path)


def test_dataset_creator_leakage_and_ai_completeness_rejected():
    row = {"source_id": "one", "creator_id": "creator", "content_category": "gaming",
           "rights_reference": "generated", "duration_ms": 1000, "dataset_split": "development"}
    with pytest.raises(ValueError, match="leakage"):
        validate_dataset({"schema_version": "chompbench-dataset/1", "sources": [row, {**row, "source_id": "two", "dataset_split": "holdout"}]})
    events = {"schema_version": "chomp-source-events/1", "benchmark_name": "ChompBench-1", **row,
              "reviewer": "fixture", "complete": True, "annotation_completeness": "complete", "annotation_origin": "ai", "events": []}
    with pytest.raises(ValueError, match="AI annotations"):
        validate_events(events)


def test_budget_reserves_final_capacity_and_reports_partial_coverage(tmp_path, monkeypatch):
    source = tmp_path / "long.mp4"
    source.write_bytes(b"fixture")
    monkeypatch.setattr("chomp.director.clip.probe", lambda p: {"duration_ms": 200_000})
    monkeypatch.setattr("chomp.director.clip.check_audio_track", lambda *a: None)
    monkeypatch.setattr("chomp.director.clip._audio_curve", lambda *a, **kw: ([], False))
    transcript = fixture_transcript(tmp_path / "words.json")
    data = json.loads(transcript.read_text(encoding="utf-8"))
    data["segments"].append({"start_ms": 190000, "end_ms": 195000, "text": "Unscanned speech."})
    write_json(transcript, data)
    report = run_clip(source, tmp_path / "jobs", fixture_campaign(), provider=ScriptedDirector(),
        transcript_path=transcript, max_calls=2, budget_policy=BudgetPolicy(discovery_calls=1, investigation_calls=0))
    assert report["status"] == "partial_review_pending"
    assert report["selected_count"] == 1
    assert report["usage"]["phase_calls"] == {"scan": 1, "decide": 1}
    assert report["coverage"]["discovery_complete"] is False
    assert report["coverage"]["scanned_utterances"] < report["coverage"]["available_utterances"]
    # The second window contains no transcript and is inspected locally;
    # the final utterance-bearing window remains unscanned.
    assert report["coverage"]["unscanned_ranges_ms"] == [[150000, 200000]]


@pytest.mark.parametrize("failure", ["timeout", "provider_error", "quota_blocked"])
def test_provider_failure_is_explicit_and_not_cached(tmp_path, monkeypatch, failure):
    from chomp.director.semantic import DirectorFailure, EditorialCalls
    from chomp.director.selection import SCAN_SCHEMA
    class Failed:
        fingerprint = {"provider": "fixture", "model": failure}
        def complete_structured(self, *args, **kwargs):
            if failure == "timeout":
                raise TimeoutError()
            return Response(failure)
    calls = EditorialCalls(Failed(), tmp_path, "source", max_calls=2)
    with pytest.raises(DirectorFailure) as error:
        calls.call("scan", "test", {}, SCAN_SCHEMA, lambda result: None)
    assert error.value.status == failure
    assert calls.sent == 1 and calls.records[0]["status"] == failure
    assert not list(tmp_path.rglob("*.json"))


def test_multimodal_registry_preserves_overlap_and_unavailable_evidence():
    source, duration = "sha256:fixture", 10_000
    one = evidence_item(source_id=source, duration_ms=duration, start_ms=1000, end_ms=3000,
                        modality="acoustic_event", audio_stream=1, labels=[{"label": "laughter", "confidence": .7}],
                        model_fingerprint={"fake": "v1"}, provenance={"test": "scripted"})
    two = evidence_item(source_id=source, duration_ms=duration, start_ms=2000, end_ms=4000,
                        modality="visual_frame", model_fingerprint={"fake": "v2"}, provenance={"test": "scripted"})
    result = collect_candidates({"audio": [{"candidate_id": "a", "range_ms": [1000, 3500], "evidence_ids": [one["id"]]}],
                                 "visual": [{"candidate_id": "b", "range_ms": [1500, 4000], "evidence_ids": [two["id"]]}]},
                                [one, two], source_id=source, duration_ms=duration)
    assert len(result["candidates"]) == 2
    assert result["candidates"][1]["lanes"] == ["visual"]
    broken = {**one, "audio_stream": 0}
    with pytest.raises(ValueError, match="stable ID"):
        validate_evidence(broken, source, duration)
    with pytest.raises(ValueError, match="source mismatch"):
        validate_evidence(one, "other", duration)


@needs_ffmpeg
def test_fake_director_complete_render_track_caption_and_review_path(tmp_path):
    source = tmp_path / "multiple tracks with spaces.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=480x270:rate=15:duration=8",
           "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=8",
           "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=8",
           "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac", str(source))
    transcript = fixture_transcript(tmp_path / "unicode.json")
    report = run_clip(source, tmp_path / "analysis", fixture_campaign(), provider=ScriptedDirector(),
                      transcript_path=transcript, audio_stream=1)
    report_path = Path(report["job_report"])
    exported = export_selection(report_path, tmp_path / "export", acknowledge_permission=True, layout="gameplay-only")
    [clip] = exported["clips"]
    technical = inspect_export(source, Path(clip["preview"]), start_ms=1000, end_ms=5000, audio_stream=1)
    assert (technical["width"], technical["height"]) == (540, 960)
    assert technical["output_dbfs"] > -40 and not technical["unexpected_silent_audio"]
    assert technical["waveform_correlation"] > .9
    assert abs(technical["stream_start_delta_ms"]) < 40
    assert len(read_manifest(exported["review_manifest"]).clips) == 1
    assert "परीक्षण" in Path(clip["captions_srt"]).read_text(encoding="utf-8")
    assert exported["manual_review_required"] and not exported["auto_publishing"]
    assert export_selection(report_path, tmp_path / "export", acknowledge_permission=True, layout="gameplay-only")["reused_job"]
    tampered = deepcopy(report)
    tampered["decisions"][0]["boundary"]["start_ms"] += 100
    with pytest.raises(ValueError, match="boundaries differ"):
        verify_selection(tampered, source, Path(report["transcript_snapshot"]))
    with pytest.raises(ValueError, match="Acknowledge"):
        export_selection(report_path, tmp_path / "no-permission", acknowledge_permission=False)


@needs_ffmpeg
def test_semantic_audio_fake_cache_track_identity_and_unavailable_state(tmp_path):
    source = vod(tmp_path / "audio.mp4", duration=3)
    class Audio:
        fingerprint = {"provider": "scripted", "weights": "none"}
        calls = 0
        def analyze(self, source, **request):
            self.calls += 1
            return {"state": "available", "events": [{"start_ms": 1000, "end_ms": 2000,
                    "labels": [{"label": "speech", "confidence": .6}, {"label": "laughter", "confidence": .8}]}]}
    provider = Audio()
    kwargs = dict(start_ms=0, end_ms=3000, audio_stream=0, rights_ref="synthetic")
    result = semantic_audio(source, tmp_path / "cache", provider, **kwargs)
    assert len(result["events"][0]["labels"]) == 2
    assert semantic_audio(source, tmp_path / "cache", provider, **kwargs)["cache_hit"]
    assert provider.calls == 1
    assert semantic_audio(source, tmp_path / "cache", None, **kwargs)["state"] == "unavailable"


@needs_ffmpeg
def test_visual_retrieval_actual_pts_budget_and_cache(tmp_path):
    source = vod(tmp_path / "frames.mp4", duration=3)
    request = dict(start_ms=0, end_ms=2500, times=[0, 1000, 2000], rights_ref="synthetic")
    result = retrieve_frames(source, tmp_path / "cache", **request)
    assert all(frame["at_ms"] >= frame["requested_ms"] for frame in result["frames"])
    again = retrieve_frames(source, tmp_path / "cache", **request)
    assert again["cache_hits"] == 3
    assert [f["sha256"] for f in result["frames"]] == [f["sha256"] for f in again["frames"]]
    with pytest.raises(ValueError, match="budget"):
        retrieve_frames(source, tmp_path / "cache", budget=RetrievalBudget(max_frames=2), **request)
    Path(result["frames"][0]["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        retrieve_frames(source, tmp_path / "cache", **request)
    assert sample_times(1000, 2000, count=2, scene_boundaries=(1500,)) == [1000, 1500]


@needs_ffmpeg
@pytest.mark.parametrize("case", ["nonzero_timestamps", "variable_frame_rate"])
def test_offset_and_vfr_end_to_end_export(tmp_path, case):
    from chomp.evaluation.smoke import run_smoke
    result = run_smoke(tmp_path / case, cases=(case,))
    assert result["passed"] == 1
    check = result["cases"][0]["checks"][0]
    assert check["waveform_correlation"] > .85
    assert abs(check["duration_ms"] - 4000) < 100


def test_failed_or_section_only_empty_analysis_does_not_claim_correct_empty(tmp_path, director_report):
    report = deepcopy(director_report)
    report.update(candidates=[], decisions=[], selected_count=0, status="budget_exhausted")
    path, events = tmp_path / "report.json", tmp_path / "events.json"
    write_json(path, report)
    write_json(events, {"schema_version": "chomp-source-events/1", "source_id": report["source_id"],
                       "duration_ms": 8000, "reviewer": "synthetic", "complete": True, "events": []})
    result = benchmark(events, path)
    assert result["correctly_empty_on_negative_source"] is None
    assert result["metrics"]["selected_clip_precision"]["value"] is None
    report.update(status="review_pending", section_ms=[1000, 7000])
    write_json(path, report)
    assert benchmark(events, path)["correctly_empty_on_negative_source"] is None


@needs_ffmpeg
@pytest.mark.parametrize("verdict", ["reject", "abstain", "zero_campaign"])
def test_nonselections_never_reach_renderer(tmp_path, monkeypatch, verdict):
    source = vod(tmp_path / "source.mp4", duration=8)
    transcript = fixture_transcript(tmp_path / "words.json")
    config = asdict(fixture_campaign())
    if verdict == "zero_campaign":
        config["clips"] = 0
    report = run_clip(source, tmp_path / "analysis", resolve_campaign(**config),
                      provider=ScriptedDirector(), transcript_path=transcript)
    if verdict != "zero_campaign":
        report["decisions"][0]["verdict"] = verdict
        report["selected_count"] = 0
        write_json(Path(report["job_report"]), report)
    def fail(*args, **kwargs):
        raise AssertionError("A nonselection reached the renderer")
    monkeypatch.setattr("chomp.director.production.render_manual_preview", fail)
    result = export_selection(Path(report["job_report"]), tmp_path / "export", acknowledge_permission=True)
    assert result["clips"] == [] and result["selected_for_review"] == 0
    assert len(read_manifest(result["review_manifest"]).clips) == 0


@needs_ffmpeg
def test_production_source_campaign_duplicate_and_evidence_guards(tmp_path):
    source = vod(tmp_path / "source.mp4", duration=8)
    report = run_clip(source, tmp_path / "analysis", fixture_campaign(), provider=ScriptedDirector(),
                      transcript_path=fixture_transcript(tmp_path / "words.json"))
    snapshot = Path(report["transcript_snapshot"])
    for changes, message in (({"source_id": "different"}, "identity"), ({"audio_stream": 7}, "unavailable"),
                             ({"transcript_snapshot_sha256": "wrong"}, "hash-matched")):
        broken = {**report, **changes}
        with pytest.raises(ValueError, match=message):
            verify_selection(broken, source, snapshot)
    broken = deepcopy(report)
    broken["campaign"]["clips"] = 0
    broken["campaign_revision"] = artifact_key(broken["campaign"])
    with pytest.raises(ValueError, match="campaign or duplicate"):
        verify_selection(broken, source, snapshot)
    broken = deepcopy(report)
    extra = deepcopy(broken["candidates"][0])
    extra["candidate_id"] = "duplicate"
    broken["candidates"].append(extra)
    extra = deepcopy(broken["decisions"][0])
    extra["candidate_id"] = "duplicate"
    broken["decisions"].append(extra)
    broken["selected_count"] = 2
    with pytest.raises(ValueError, match="campaign or duplicate"):
        verify_selection(broken, source, snapshot)


@needs_ffmpeg
def test_benchmark_rejects_wrong_technical_intervals(tmp_path):
    from chomp.evaluation.smoke import run_smoke
    result = run_smoke(tmp_path / "smoke", cases=("unicode_captions",))
    root = tmp_path / "smoke/unicode_captions"
    measurement = json.loads((root / "technical.json").read_text(encoding="utf-8"))
    measurement["clips"][0]["start_ms"] += 1
    write_json(root / "wrong-technical.json", measurement)
    with pytest.raises(ValueError, match="interval/identity"):
        benchmark(root / "events.json", Path(result["cases"][0]["director_report"]), technical_path=root / "wrong-technical.json")


@needs_ffmpeg
def test_interrupted_export_resumes_and_corrupt_cached_artifacts_fail_closed(tmp_path, monkeypatch):
    from chomp.director import production
    source = vod(tmp_path / "source.mp4", duration=8)
    report = run_clip(source, tmp_path / "analysis", fixture_campaign(), provider=ScriptedDirector(),
                      transcript_path=fixture_transcript(tmp_path / "words.json"))
    original_write = production.write_json
    def interrupted(path, data, **kwargs):
        if path.name == "review_queue.json":
            raise OSError("simulated interruption before final manifest")
        return original_write(path, data, **kwargs)
    monkeypatch.setattr(production, "write_json", interrupted)
    kwargs = dict(acknowledge_permission=True, layout="gameplay-only")
    with pytest.raises(OSError, match="simulated interruption"):
        export_selection(Path(report["job_report"]), tmp_path / "export", **kwargs)
    [media] = list((tmp_path / "export").rglob("*.mp4"))
    before = digest_file(media)
    monkeypatch.setattr(production, "write_json", original_write)
    completed = export_selection(Path(report["job_report"]), tmp_path / "export", **kwargs)
    assert digest_file(media) == before and completed["selected_for_review"] == 1
    media.write_bytes(b"corrupted after export")
    with pytest.raises(ValueError, match="changed"):
        export_selection(Path(report["job_report"]), tmp_path / "export", **kwargs)
