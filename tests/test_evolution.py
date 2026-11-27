from pathlib import Path
from types import SimpleNamespace

import pytest

from chomp.artifacts import write_json
from chomp.director.provider import Response
from chomp.evolution import EvolutionConfig, run_evolution, segment_and_discover
from chomp.evolution_context import Proposal, close, investigate
from chomp.evolution_feedback import load_model, train
from chomp.evolution_memory import Memory
from chomp.evolution_reasoning import InvestigationBudget, Reasoner
from chomp.models import TranscriptSegment
from chomp.research_interfaces import evidence_item


IDENTITY = {"source_id": "test-vod", "duration_ms": 100_000}


def speech(start, end, text, speaker="one"):
    return evidence_item(**IDENTITY, start_ms=start, end_ms=end, modality="utterance",
                         model_fingerprint={"fixture": True}, provenance={"original": "test"},
                         text=text, speaker_id=speaker)


def store(*rows):
    return Memory(list(rows), **IDENTITY)


def test_global_memory_preserves_repeated_occurrences_and_filters_scope():
    one = speech(100, 500, "The blue envelope arrived.")
    two = speech(55_000, 56_000, "The blue envelope arrived.", "two")
    memory = store(one, two)
    assert one["id"] != two["id"]
    assert {r["evidence_id"] for r in memory.search("blue envelope")} == {one["id"], two["id"]}
    assert memory.search("blue envelope", scope="future", anchor=1000)[0]["evidence_id"] == two["id"]
    assert memory.search("blue envelope", scope="past", anchor=1000)[0]["evidence_id"] == one["id"]
    assert memory.search("blue envelope", speaker="two")[0]["evidence_id"] == two["id"]
    assert memory.hydrate([one["id"]]) == [one]
    memory.search("blue envelope", speaker="two")
    assert len(memory.history) == 4


def test_adjacent_qualification_and_distant_alternative_apply_to_closure():
    core = speech(50_000, 58_000, "I always recommend this expensive blue envelope.")
    qualifier = speech(59_000, 64_000, "But only if you can afford it.")
    distant = speech(1_000, 8_000, "The blue envelope was free at the library.")
    memory = store(core, qualifier, distant)
    proposal = Proposal("dialogue", (core["id"],), 50_000, 58_000, .8, "advice")
    investigation = investigate(memory, proposal, True, False)
    assert investigation["viewer_required_groups"] == [[qualifier["id"]]]
    assert investigation["queries"][0]["hits"][0]["evidence_id"] == distant["id"]
    decision, choices = close(memory, proposal, [[distant["id"]], [qualifier["id"]]], duration=100_000,
                              minimum=6_000, maximum=25_000, audio=[], hop=250, snap=True)
    assert (decision.start_ms, decision.end_ms) == (50_000, 64_000)
    assert choices[0]["state"] == "no_contiguous_interval"
    assert close(memory, proposal, [[distant["id"]]], duration=100_000,
                 minimum=6_000, maximum=25_000, audio=[], hop=250, snap=False)[0] is None


def test_distant_callback_and_correction_remain_unresolved():
    setup = speech(100, 5000, "The blue envelope contains a note.")
    payoff = speech(50_000, 52_000, "That blue envelope was the surprise.")
    correction = speech(90_000, 95_000, "Actually the blue envelope was a decoy.")
    memory = store(setup, payoff, correction)
    proposal = Proposal("dialogue", (payoff["id"],), 50_000, 52_000, .8, "surprise")
    result = investigate(memory, proposal, True, False)
    assert set(result["risks"]) == {"unresolved_reference", "possible_later_qualification"}
    assert {r["kind"] for r in result["relationships"]} == {"callback", "qualification"}
    assert result["viewer_required_groups"] == [[]]


def test_visual_and_dialogue_remain_independent_at_same_time():
    light = speech(5000, 12_000, "I realized why the library keeps envelopes in those books.")
    motion = evidence_item(**IDENTITY, start_ms=5000, end_ms=7500, modality="motion",
                           model_fingerprint={"fixture": True}, provenance={"original": "test"}, strength=.5)
    proposals, units = segment_and_discover(store(light, motion), IDENTITY, {"dialogue", "visual", "exploration"}, 6, 40_000)
    assert any(p.core_ids == (light["id"],) and p.lane == "dialogue" for p in proposals)
    assert any(p.core_ids == (motion["id"],) and p.lane == "visual" for p in proposals)
    assert {u["level"] for u in units} == {1, 2, 3}


def test_no_interest_and_optional_model_failure_are_reported(tmp_path: Path):
    records, selected, report = run_evolution(
        EvolutionConfig(disabled=("visual",), embeddings=tmp_path / "nonexistent"),
        source=tmp_path / "none", source_id=IDENTITY["source_id"], duration=100_000, cache=tmp_path,
        transcript=[TranscriptSegment(50_000, 56_000, "Thanks for watching.")], turns=[], observations=[],
        audio=[], audio_stream=0, audio_hop=250, asr={"fixture": True}, provider=None,
        proposals_per_hour=40, limit=3, min_score=45,
    )
    assert not selected
    assert report["availability"]["dense"]["state"] == "failed"
    assert report["availability"]["visual"]["state"] == "unavailable"
    assert {"rejected", "unresolved"} >= {record["decision"] for _, record in records}


class Provider:
    fingerprint = {"producer": "test-double"}
    calls = 0

    def complete_structured(self, prompt, contract, *, timeout):
        self.calls += 1
        return Response("response_received", decision={"candidate_id": "test", "action": "select",
                        "rationale": "fixture", "supporting_ids": ["invalid"], "critical_issues": [], "score": 80})


def test_provider_fabricated_reference_and_reserved_final_budget(tmp_path):
    provider = Provider()
    payload = {"candidate_id": "test", "evidence": [{"id": "real"}]}
    reasoner = Reasoner(provider, tmp_path, 2)
    with pytest.raises(ValueError, match="unavailable evidence"):
        reasoner.call("final", payload)
    with pytest.raises(InvestigationBudget):
        reasoner.call("investigation", payload)
    assert provider.calls == 1
    assert reasoner.errors == 1


def test_synthetic_training_excludes_technical_and_blocks_live_activation(tmp_path):
    def label(i, good, reason="interest"):
        return {"source_id": str(i), "candidate_id": str(i), "reviewer": "fixture", "origin": "synthetic",
                "reason": reason, "decision": "good" if good else "bad", "features": [float(good)] + [0.] * 8}
    feedback = tmp_path / "feedback.json"
    write_json(feedback, {"schema_version": "chomp-evolution-feedback/1",
                         "records": [*(label(i, i % 2) for i in range(6)), label(10, False, "audio")]})
    result = train(feedback, tmp_path, synthetic=True)
    assert result["examples"] == 5
    assert result["holdout_metrics"]["examples"] == 1
    with pytest.raises(ValueError, match="human-trained"):
        load_model(Path(result["model"]))
