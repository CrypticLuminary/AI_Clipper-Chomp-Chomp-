"""EDIT-01 contract, source-image provenance, and bounded provider regressions."""
from __future__ import annotations

from hashlib import sha256
import json
from subprocess import CompletedProcess

import pytest

from chomp.artifacts import digest_file, write_json
from chomp.director.contract import bundle_digest, read_json, schema, validate_plan
from chomp.director.evidence import build_bundle, read_verified_bundle
from chomp.director.experiment import compare
from chomp.director.provider import CodexCLI, Response, decision_schema

from test_stage1_integration import needs_ffmpeg, vod

Draft202012Validator = pytest.importorskip("jsonschema").Draft202012Validator


@pytest.fixture
def evidence(tmp_path):
    source = vod(tmp_path / "fixture.mp4", duration=5)
    path = build_bundle(source, tmp_path / "bundle", case_id="fixture", split="development",
                        rights_ref="synthetic", candidate_id="event", start_ms=500, end_ms=3500,
                        anchor_ms=1500, frame_times=(1500, 2100))
    return source, path, read_json(path)


def _decision(mode):
    selected = mode == "frames"
    return {
        "schema_version": "chomp-editorial/1", "decision": "select" if selected else "abstain",
        "decision_reason": "Compare a sampled source view.", "categories": ["other"], "confidence": .6,
        "claims": [{"modality": "visual", "support": "observed", "statement": "A source frame is present.",
                    "evidence_ids": ["f1"]}] if selected else [],
        "moment": {"start_ms": 500, "end_ms": 3500, "anchor_ms": 1500, "evidence_ids": ["f1"]} if selected else None,
        "beats": [{"start_ms": 1400, "end_ms": 1650, "role": "event", "description": "Source view.",
                   "evidence_ids": ["f1"]}] if selected else [],
        "shots": [{"start_ms": 500, "end_ms": 3500, "layout": "main_only", "target_subject_ids": ["scene"],
                   "required_region_ids": ["f1-scene"], "crop_behavior": "hold", "reason": "Full appearance.",
                   "evidence_ids": ["f1"], "uncertain": False}] if selected else [],
        "title": None, "hook": None, "captions": {"mode": "verify_speech", "emphasis_word_ids": []},
        "requires_visual_verification": not selected, "unresolved": [],
    }


def _plan(bundle, mode="frames"):
    return {
        **_decision("frames"), "review_status": "pending", "publish_status": "disabled",
        "provenance": {"bundle_sha256": bundle["evidence_hash"], "source_sha256": bundle["source"]["sha256"],
                       "mode": mode, "provider": "fixture", "model": "fixture", "cli_version": None, "prompt_sha256": "0" * 64},
    }


def test_schemas_are_valid_json_schema():
    Draft202012Validator.check_schema(schema("evidence_bundle"))
    Draft202012Validator.check_schema(schema("editorial_plan"))
    Draft202012Validator.check_schema(decision_schema())


@needs_ffmpeg
@pytest.mark.parametrize("mutation,message", [
    (lambda plan: plan["claims"][0].update(evidence_ids=["forged"]), "visible in this mode"),
    (lambda plan: plan["moment"].update(end_ms=5050), "source-relative span"),
    (lambda plan: plan["shots"][0].update(start_ms=550), "gaps or overlap"),
    (lambda plan: plan["shots"][0].update(layout="streamer_only"), "required source region"),
    (lambda plan: plan["shots"][0].update(required_region_ids=["f2-scene"]), "cited, in-range frames"),
    (lambda plan: plan["beats"][0].update(evidence_ids=["f2"]), "outside the selected cut"),
    (lambda plan: plan["provenance"].update(mode="text"), "visible in this mode"),
    (lambda plan: plan["captions"].update(emphasis_word_ids=["forged"]), "word timing"),
    (lambda plan: plan.update(publish_status="enabled"), "publish_status"),
])
def test_plan_rejects_unsupported_clock_and_evidence(evidence, mutation, message):
    _, _, bundle = evidence
    plan = _plan(bundle)
    validate_plan(plan, bundle)
    mutation(plan)
    with pytest.raises(ValueError, match=message):
        validate_plan(plan, bundle)


@needs_ffmpeg
@pytest.mark.parametrize("tamper", ["image", "timestamp"])
def test_hashes_alone_cannot_forge_a_source_frame(evidence, tamper):
    source, path, bundle = evidence
    read_verified_bundle(path, source)
    frame = bundle["visual_samples"][0]
    if tamper == "image":
        fabricated = b"forged image content"
        (path.parent / frame["path"]).write_bytes(fabricated)
        frame["sha256"] = sha256(fabricated).hexdigest()
    else:
        frame["at_ms"] += 40
    bundle["evidence_hash"] = bundle_digest(bundle)
    write_json(path, bundle)
    with pytest.raises(ValueError, match="does not match a decoded source image"):
        read_verified_bundle(path, source)


@needs_ffmpeg
def test_matched_request_supports_visual_candidates_without_transcript_or_chat(evidence, tmp_path):
    source, path, frozen = evidence
    original = digest_file(source)

    class FixtureProvider:
        name = "stub_provider"
        model = "fixture"
        timeout = 25

        def __init__(self):
            self.calls = []

        def complete(self, prompt, images):
            payload = json.loads(prompt.split("\nEVIDENCE_JSON\n")[1])
            self.calls.append((payload, images))
            return Response("response_received", decision=_decision(payload["mode"]), cli_version="fixture 1")

    provider = FixtureProvider()
    out = tmp_path / "experiment"
    report = compare(path, source, out, provider)
    assert [row["decision"] for row in report["results"]] == ["abstain", "select"]
    assert report["review_status"] == "pending" and report["publish_status"] == "disabled"
    (text, no_images), (visible, images) = provider.calls
    assert not no_images and not text["visual_samples"] and len(images) == 2
    assert not text["modality_flags"]["transcript"] and not text["modality_flags"]["chat"]
    for key in ("candidate", "context", "utterances", "audio_events", "chat_events"):
        assert text[key] == visible[key] == frozen[key]
    assert "provenance" not in read_json(out / "blind-review" / "A.json")
    assert compare(path, source, out, provider) == report
    assert len(provider.calls) == 2 and digest_file(source) == original


def test_cli_restricts_tools_and_preserves_quota_status(monkeypatch):
    monkeypatch.setattr("chomp.director.provider.shutil.which", lambda _: "codex.exe")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-no-transport")
    invocations = []

    def run(args, **kwargs):
        if args[-1] == "--version":
            return CompletedProcess(args, 0, stdout="codex-cli fixture\n", stderr="")
        if "login" in args:
            return CompletedProcess(args, 0, stdout="", stderr="Logged in using ChatGPT")
        invocations.append(args)
        assert args[args.index("--sandbox") + 1] == "read-only"
        assert "shell_tool" in args and "unified_exec" in args and args[args.index("-a") + 1] == "never"
        assert kwargs["env"].get("OPENAI_API_KEY") is None
        return CompletedProcess(args, 1, stdout="", stderr="HTTP 429: usage limit")

    monkeypatch.setattr("chomp.director.provider.subprocess.run", run)
    result = CodexCLI("fixture", timeout=15).complete("evidence", [])
    assert result.status == "quota_blocked"
    assert result.cli_version == "codex-cli fixture"
    assert len(invocations) == 1
