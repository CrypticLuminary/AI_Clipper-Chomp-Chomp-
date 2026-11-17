"""Regressions for independent discovery, evidence grounding, and resumable Director jobs."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from chomp.artifacts import write_json
from chomp.director.campaign import resolve_campaign
from chomp.director.clip import run_clip
from chomp.director.provider import Response
from chomp.director.selection import TranscriptIndex
from chomp.director.semantic import enforce
from chomp.models import TranscriptSegment

FIXTURE = Path(__file__).parent / "fixtures" / "director-first"


def decision(cid, evidence=(), *, verdict="select"):
    return {"candidate_id": cid, "verdict": verdict, "event": "The envelope mystery",
            "viewer_value": "The surprise has a satisfying explanation.", "reason": "The setup reaches a payoff.",
            "audience": "Story listeners", "campaign_fit": "A standalone story", "confidence": "medium",
            "evidence_ids": list(evidence), "setup_ids": [], "development_ids": [], "payoff_ids": [],
            "title_suggestion": "The Blue Envelope", "hook_suggestion": None}


class FixtureDirector:
    fingerprint = {"provider": "fixture", "model": "scripted"}

    def __init__(self):
        self.calls = []

    def complete_structured(self, prompt, contract, *, timeout):
        payload = json.loads(prompt.split("Evidence and campaign JSON\n", 1)[1])
        self.calls.append(payload)
        assert 0 < timeout <= 1800
        if "window" in payload:
            start = payload["window"][0]
            proposals = leads = []
            if start == 0:
                proposals = [{"event": "Blue envelopes were a child's reading reports.",
                              "viewer_value": "An ordinary lost envelope turns into a family story.",
                              "evidence_ids": ["u1", "u2", "u3"]}]
                leads = [{"note": "The recurring alarm is an unresolved setup.", "evidence_ids": ["u4"]}]
            if start == 120_000:
                assert [row["id"] for row in payload["earlier_evidence"]] == ["u4"]
                proposals = [{"event": "Moving the alarm led to more interruptions.",
                              "viewer_value": "The plan backfires.", "evidence_ids": ["u4", "u6"]}]
            result = {"proposals": proposals, "leads": leads}
        elif not payload["retrieved"]:
            result = {"requests": [{"start_ms": 131000, "end_ms": 135000, "reason": "Check for a bridging line."}],
                      "decisions": []}
        else:
            assert payload["retrieved"][0]["status"] == "unavailable"
            result = {"requests": [], "decisions": [
                decision("d1", ["u1", "u2", "u3"]), decision("d2", ["u4", "u6"]),
            ]}
        return Response("response_received", decision=result, usage={"input_tokens": 100, "output_tokens": 9})


@pytest.fixture
def source_job(tmp_path, monkeypatch):
    source = tmp_path / "source with spaces.mp4"
    source.write_bytes(b"stub media")
    monkeypatch.setattr("chomp.director.clip.probe", lambda path: {"duration_ms": 160_000})
    monkeypatch.setattr("chomp.director.clip.check_audio_track", lambda meta, track: None)
    # No audio or chat proposals. Every quiet discovery must come from the transcript.
    monkeypatch.setattr("chomp.director.clip._audio_curve", lambda *a, **kw: ([], False))
    campaign = resolve_campaign(FIXTURE / "campaign.json")

    def run(provider, **kwargs):
        return run_clip(source, tmp_path / "jobs", kwargs.pop("campaign", campaign), provider=provider,
                        transcript_path=FIXTURE / "transcript.json", **kwargs)

    return run, campaign


def test_quiet_recall_cross_window_memory_and_hard_maximum(source_job):
    run, _ = source_job
    provider = FixtureDirector()
    result = run(provider)
    assert result["status"] == "review_pending"
    assert result["coverage"]["scanned_utterances"] == result["coverage"]["available_utterances"] == 6
    assert result["coverage"]["no_transcript_ranges_ms"][0] == [0, 10_000]
    assert result["candidates"][0]["lanes"] == ["semantic"]
    quiet, timer = result["decisions"]
    assert result["selected_count"] == 1
    assert (quiet["boundary"]["start_ms"], quiet["boundary"]["end_ms"]) == (10_000, 37_200)
    assert [row["id"] for row in quiet["evidence"]] == ["u1", "u2", "u3"]
    assert timer["constraint_violation"] == "outside_campaign_duration"
    assert result["investigation"][0]["status"] == "unavailable"
    assert result["usage"]["provider_calls"] == 5
    html = Path(result["review_page"]).read_text(encoding="utf-8")
    assert "Blue Envelope" in html and "Human review pending" in html
    assert "#t=10.000,37.200" in html


def test_partial_results_resume_and_cache_invalidates_by_brief_and_model(source_job):
    run, campaign = source_job
    provider = FixtureDirector()
    failed = run(provider, max_calls=1)
    assert failed["status"] == "budget_exhausted"
    assert failed["selected_count"] == 0
    assert 0 < failed["coverage"]["scanned_utterances"] < 6
    finished = run(FixtureDirector())
    assert finished["selected_count"] == 1
    assert finished["usage"]["provider_calls"] == 4
    reuse = run(FixtureDirector())
    assert reuse["usage"]["provider_calls"] == 0
    assert all(call["cached"] for call in reuse["usage"]["calls"])
    different = run(FixtureDirector(), campaign=replace(campaign, brief="New editorial brief"))
    assert different["campaign_revision"] != finished["campaign_revision"]
    assert different["usage"]["provider_calls"] == 5
    new_provider = FixtureDirector()
    new_provider.fingerprint = {"provider": "fixture", "model": "other"}
    assert run(new_provider)["usage"]["provider_calls"] == 5


@pytest.mark.parametrize("result", [
    {"proposals": [{"event": "Invented", "viewer_value": "Curiosity", "evidence_ids": ["u999"]}], "leads": []},
    {"proposals": ["unstructured"], "leads": []},
    {"proposals": [], "leads": [], "unexpected": "extra"},
])
def test_invalid_responses_fail_closed_and_are_not_cached(source_job, result):
    run, _ = source_job

    class Bad(FixtureDirector):
        def complete_structured(self, *args, **kwargs):
            return Response("response_received", decision=result)

    failed = run(Bad())
    assert failed["status"] == "invalid_response"
    assert not failed["selected_count"] and failed["review_page"] is None
    assert run(FixtureDirector())["usage"]["provider_calls"] == 5


def test_empty_director_and_zero_clip_maximum(source_job):
    run, campaign = source_job

    class Empty(FixtureDirector):
        def complete_structured(self, *args, **kwargs):
            return Response("response_received", decision={"proposals": [], "leads": []})

    empty = run(Empty())
    assert empty["status"] == "review_pending" and empty["selected_count"] == 0
    assert empty["coverage"]["scanned_utterances"] == 6
    zero = run(FixtureDirector(), campaign=replace(campaign, clips=0))
    assert zero["status"] == "review_pending" and zero["selected_count"] == 0
    assert all(row["verdict"] == "abstain" for row in zero["decisions"])


def test_campaign_overrides_and_literal_exclusions(tmp_path):
    path = tmp_path / "campaign.json"
    write_json(path, {"rights_ref": "test", "max_duration": 26, "excluded_topics": ["Video Game"]})
    campaign = resolve_campaign(path, brief="Find original stories")
    assert campaign.durations_ms == (20_000, 26_000, 26_000)
    assert campaign.brief == "Find original stories"
    assert campaign.violation(0, 20_000, "A VIDEO, game series") == "excluded_transcript_phrase"
    assert campaign.violation(0, 20_000, "Three video gamers") is None
    assert resolve_campaign(FIXTURE / "campaign.json", max_duration=24).duration == 24
    with pytest.raises(ValueError, match="human_review"):
        resolve_campaign(path, human_review=False)


def test_overlapping_speech_expands_boundaries_before_constraints(source_job):
    _, campaign = source_job
    index = TranscriptIndex([
        TranscriptSegment(9_000, 11_000, "Leading speech"),
        TranscriptSegment(10_000, 20_000, "First cited utterance"),
        TranscriptSegment(25_000, 30_000, "Last cited utterance"),
        TranscriptSegment(29_000, 42_000, "Trailing speech"),
    ])
    rows = enforce([{"candidate_id": "d1", "range_ms": [10_000, 30_000], "evidence_ids": ["u2"]}],
                   [decision("d1", ["u2", "u3"])], index, (0, 50_000),
                   replace(campaign, duration=30, max_duration=30))
    item = rows[0]
    assert (item["boundary"]["start_ms"], item["boundary"]["end_ms"]) == (9_000, 42_000)
    assert item["verdict"] == "reject" and item["constraint_violation"] == "outside_campaign_duration"
