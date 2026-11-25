"""BASE-10-R1 independent-review regressions. Providers are deterministic fakes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from chomp.artifacts import write_json
from chomp.director.campaign import resolve_campaign
from chomp.director.clip import run_clip
from chomp.director.production import export_selection
from chomp.director.provider import Response
from chomp.director.semantic import BudgetPolicy
from chomp.evaluation.benchmark import benchmark
from chomp.evaluation.core import make_template, read_manifest
from chomp.research_interfaces import collect_candidates, evidence_item
from test_cycle2 import ScriptedDirector, fixture_campaign, fixture_transcript
from test_stage1_integration import needs_ffmpeg, vod


def test_default_allocations_reserve_investigation_and_final_response():
    assert BudgetPolicy.for_total(120) == BudgetPolicy(115, 4, 1)
    for total in range(1, 301):
        policy = BudgetPolicy.for_total(total)
        policy.validate(total)
        assert policy.discovery_calls + policy.investigation_calls + policy.final_reserve == total
        assert policy.final_reserve >= 1
        if total >= 3:
            assert policy.investigation_calls >= 1
    with pytest.raises(ValueError, match="both investigation"):
        BudgetPolicy(119, 4, 1).validate(120)


def test_cli_uses_same_default_budget(monkeypatch):
    from chomp.director import clip
    captured = {}
    def run(*args, **kwargs):
        captured.update(kwargs)
        return {"status": "review_pending", "job_report": "fixture", "review_page": None, "selected_count": 0,
                "coverage": {"available_utterances": 0, "scanned_utterances": 0}, "decisions": []}
    monkeypatch.setattr(clip, "run_clip", run)
    assert clip.main(["fixture.mp4", "--director", "heuristic", "--rights-ref", "synthetic"]) == 0
    assert captured["budget_policy"] == BudgetPolicy(115, 4, 1)


def test_long_speech_scan_has_four_context_reads_then_final_and_cached_resume(tmp_path, monkeypatch):
    source = tmp_path / "two-hour-source.mp4"
    source.write_bytes(b"synthetic stub")
    duration = 7_200_000
    monkeypatch.setattr("chomp.director.clip.probe", lambda p: {"duration_ms": duration})
    monkeypatch.setattr("chomp.director.clip.check_audio_track", lambda *args: None)
    monkeypatch.setattr("chomp.director.clip._audio_curve", lambda *args, **kwargs: ([], False))
    transcript = tmp_path / "transcript.json"
    write_json(transcript, {"segments": [{"start_ms": i*60000+1000, "end_ms": i*60000+21000,
                                         "text": f"Long speech fixture utterance {i}"} for i in range(120)]})
    class ContextDirector:
        fingerprint = {"provider": "fixture", "model": "four-context-reads"}
        def complete_structured(self, prompt, contract, *, timeout):
            payload = json.loads(prompt.split("Evidence and campaign JSON\n")[1])
            if "window" in payload:
                return Response("response_received", decision={"proposals": [{"event": "fixture", "viewer_value": "test",
                    "evidence_ids": ["u1"]}] if payload["window"][0] == 0 else [], "leads": []})
            n = len(payload["retrieved"])
            assert payload["remaining_reads"] == 4-n
            if n < 4:
                return Response("response_received", decision={"requests": [{"start_ms": 32000+n*1000,
                    "end_ms": 32500+n*1000, "reason": "Bounded context read"}], "decisions": []})
            return Response("response_received", decision={"requests": [], "decisions": [{
                "candidate_id": "d1", "verdict": "select", "event": "fixture", "viewer_value": "test",
                "reason": "Scripted temporal fixture, contextual quality unmeasured", "audience": "testers",
                "campaign_fit": "fixture", "confidence": "low", "evidence_ids": ["u1"], "setup_ids": ["u1"],
                "development_ids": [], "payoff_ids": [], "title_suggestion": None, "hook_suggestion": None}]})
    campaign = resolve_campaign(rights_ref="synthetic", min_duration=1, duration=20, max_duration=30)
    def run():
        return run_clip(source, tmp_path / "analysis", campaign, provider=ContextDirector(), transcript_path=transcript)
    first = run()
    assert first["status"] == "partial_review_pending" and first["selected_count"] == 1
    assert first["usage"]["phase_calls"] == {"scan": 115, "decide": 5}
    assert len(first["investigation"]) == 4 and first["usage"]["provider_calls"] == 120
    assert first["usage"]["calls"][-1]["purpose"] == "final_decision"
    resumed = run()
    assert resumed["status"] == "review_pending" and resumed["coverage"]["discovery_complete"]
    assert resumed["usage"]["provider_calls"] == 5
    assert all(row["cached"] for row in resumed["usage"]["calls"] if row["phase"] == "decide")
    replay = run()
    assert replay["usage"]["provider_calls"] == 0 and replay["selected_count"] == 1


def metric_fixture(tmp_path):
    gold = {"schema_version": "chomp-source-events/1", "source_id": "synthetic", "duration_ms": 40000,
            "reviewer": "synthetic fixture", "complete": True, "events": [{"id": "event", "core_ms": [6000, 9000],
            "essential_ms": [1000, 12000], "start_range_ms": [0, 1000], "end_range_ms": [12000, 13000]}]}
    ledger = {"schema_version": "chomp-candidates/1", "source_id": "synthetic", "source": "fixture.mp4",
              "job_id": "run", "duration_ms": 40000, "candidates": [
        {"candidate_id": str(i), "decision": "selected", "proposal": {"start_ms": a, "end_ms": b},
         "candidate": {"start_ms": a, "end_ms": b}} for i,(a,b) in enumerate(((0,12000),(5000,11000),(30000,34000)))]}
    events, candidates = tmp_path / "events.json", tmp_path / "candidates.json"
    write_json(events, gold)
    write_json(candidates, ledger)
    return events, candidates, gold


def test_event_and_clip_completeness_have_different_denominators(tmp_path):
    events, ledger, gold = metric_fixture(tmp_path)
    result = benchmark(events, ledger)
    metrics = result["metrics"]
    assert metrics["event_essential_span_coverage"]["value"] == 1
    assert metrics["event_essential_span_coverage"]["denominator"] == 1
    assert metrics["matched_clip_essential_span_coverage"] == {"numerator": 1, "denominator": 2, "value": .5}
    assert result["unmatched_selections"] == 1 and metrics["unmatched_selection_rate"]["value"] == 1/3
    assert result["selected_clip_matches"][2]["essential_preserved"] is None
    assert metrics["human_reviewed_coverage"]["value"] is None
    assert metrics["human_context_completeness"]["value"] is None
    assert metrics["selected_clip_precision"]["value"] is None
    gold["complete"] = False
    write_json(events, gold)
    assert benchmark(events, ledger)["metrics"]["unmatched_selection_rate"]["value"] is None


@pytest.mark.parametrize("legacy", [True, False])
def test_audio_envelope_alignment_never_populates_actual_av_sync(tmp_path, legacy):
    events, ledger, _ = metric_fixture(tmp_path)
    technical = tmp_path / "technical.json"
    write_json(technical, {"schema_version": "chompbench-technical/1", "source_id": "synthetic", "job_id": "run",
        "duration_ms": 40000, "clips": [{"candidate_id": "0", "start_ms": 0, "end_ms": 12000,
            "measurements": {"envelope_lag_ms" if legacy else "audio_envelope_lag_ms": -20}}]})
    metrics = benchmark(events, ledger, technical_path=technical)["metrics"]
    assert metrics["audio_envelope_absolute_lag_ms"]["value"] == 20
    assert metrics["audio_envelope_absolute_lag_ms"]["is_audiovisual_sync"] is False
    assert metrics["av_sync_error_ms"]["value"] is None


def test_lane_order_overflow_is_opt_in_and_excluded_lanes_are_reported():
    evidence = evidence_item(source_id="source", duration_ms=10000, start_ms=1000, end_ms=3000,
        modality="utterance", model_fingerprint={"fake": "v1"}, provenance={"test": "synthetic"})
    def row(cid): return {"candidate_id": cid, "range_ms": [1000,3000], "evidence_ids": [evidence["id"]]}
    lanes = {"energy": [row("one"),row("two")], "dialogue": [row("three")], "visual": []}
    kwargs = dict(source_id="source", duration_ms=10000, max_candidates=2)
    with pytest.raises(ValueError, match="experimental"):
        collect_candidates(lanes, [evidence], **kwargs)
    result = collect_candidates(lanes, [evidence], experimental_lane_order=True, **kwargs)
    assert result["experimental"] and not result["production_fusion_enabled"]
    assert result["allocation_diagnostics"]["completely_excluded_lanes"] == ["dialogue"]
    assert result["allocation_diagnostics"]["empty_input_lanes"] == ["visual"]
    assert result["allocation_diagnostics"]["omitted_by_lane"] == {"energy": 0, "dialogue": 1, "visual": 0}
    reverse = collect_candidates(dict(reversed(list(lanes.items()))), [evidence], experimental_lane_order=True, **kwargs)
    assert reverse["allocation_diagnostics"]["completely_excluded_lanes"] == []
    zero = collect_candidates(lanes, [evidence], experimental_lane_order=True, **{**kwargs,"max_candidates":0})
    assert zero["allocation_diagnostics"]["completely_excluded_lanes"] == ["energy","dialogue"]


@pytest.fixture
def rendered_cache(tmp_path):
    source = vod(tmp_path / "source.mp4", duration=8)
    report = run_clip(source, tmp_path / "analysis", fixture_campaign(), provider=ScriptedDirector(),
                      transcript_path=fixture_transcript(tmp_path / "transcript.json"))
    report_path = Path(report["job_report"])
    out = tmp_path / "exports"
    manifest = export_selection(report_path, out, acknowledge_permission=True, layout="gameplay-only")
    return report_path, out, manifest


@needs_ffmpeg
@pytest.mark.parametrize("mutation", [
    lambda m: m.update(clips=[]),
    lambda m: m.update(clips=[], selected_for_review=0),
    lambda m: m["clips"].append(deepcopy(m["clips"][0])),
    lambda m: m["clips"][0].update(candidate_id="unknown"),
    lambda m: m.update(source_id="wrong"),
    lambda m: m["clips"][0].update(source_id="wrong"),
    lambda m: m["clips"][0].update(start_ms=1100),
    lambda m: m["clips"][0]["candidate"].update(end_ms=4900),
    lambda m: m["clips"][0].update(preview=None),
    lambda m: m["clips"][0].update(captions_srt=None),
    lambda m: m["clips"][0].update(caption_cues=0),
    lambda m: m["clips"][0]["artifact_sha256"].update(captions_srt=None),
    lambda m: m["clips"][0].update(audio_stream=1),
    lambda m: m.update(campaign={}),
])
def test_cached_export_rejects_missing_duplicate_and_modified_manifest_entries(rendered_cache, mutation):
    report, out, manifest = rendered_cache
    modified = deepcopy(manifest)
    mutation(modified)
    write_json(Path(manifest["review_manifest"]), modified)
    with pytest.raises(ValueError):
        export_selection(report, out, acknowledge_permission=True, layout="gameplay-only")


@needs_ffmpeg
def test_human_review_coverage_is_separate_from_span_coverage(rendered_cache, tmp_path):
    report_path, _, manifest = rendered_cache
    report = json.loads(report_path.read_text(encoding="utf-8"))
    events = tmp_path / "events.json"
    write_json(events, {"schema_version": "chomp-source-events/1", "source_id": report["source_id"],
        "duration_ms": 8000, "reviewer": "synthetic", "complete": True, "events": []})
    review = tmp_path / "review.json"
    make_template(read_manifest(manifest["review_manifest"]), review)
    result = benchmark(events, report_path, manifest_path=Path(manifest["review_manifest"]), reviews=(review,))
    assert result["metrics"]["human_reviewed_coverage"][0]["value"] == 0
    assert result["metrics"]["selected_clip_precision"][0]["value"] is None
    assert result["metrics"]["human_context_completeness"][0]["mean_1_to_5"] is None
