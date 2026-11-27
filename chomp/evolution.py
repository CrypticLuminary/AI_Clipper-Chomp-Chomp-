"""Opt-in EVOLUTION discovery, investigation and review selection."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from math import ceil
from pathlib import Path
import re
from time import perf_counter

from .artifacts import artifact_key
from .evolution_context import Proposal, close, investigate, repeated_story
from .evolution_memory import Memory, fused_score, reciprocal_ranks, terms
from .evolution_perception import visual_frames, visual_motion, yamnet
from .evolution_reasoning import InvestigationBudget, Reasoner, evidence_view, provider_investigation
from .models import Candidate
from .research_interfaces import evidence_item

VERSION = "evolution/1"
PROFILES = {"eco": (8, 2500, 2800, 2800), "balanced": (16, 1250, 5600, 5600),
            "gpu": (24, 750, 9000, 9000)}
LANES = ("dialogue", "acoustic", "visual", "chat", "exploration")
PAYOFF = re.compile(r"\b(?:realized|learned|figured out|turns out|found out|lesson|never expected|first time|the reason|what matters|after that|that was when|because|instead|finally|the point|you can|you don't have to)\b", re.I)
ROUTINE = re.compile(r"^\W*(?:thanks for watching|hey everyone|welcome back|sorry that was loud|hold on|give me a second|one second)\b", re.I)


@dataclass(frozen=True)
class EvolutionConfig:
    profile: str = "eco"
    disabled: tuple[str, ...] = ()
    embeddings: Path | None = None
    audio_model: Path | None = None
    min_ms: int = 6_000
    max_ms: int = 45_000
    max_frames: int | None = None
    max_audio_windows: int | None = None
    memory_mb: int = 128
    reasoning_calls: int = 8
    reasoning_seconds: int = 600
    model: Path | None = None

    def __post_init__(self):
        if self.profile not in PROFILES or set(self.disabled) - {*LANES, "memory", "hybrid", "snap", "local-review", "quiet-asr"}:
            raise ValueError("Unknown EVOLUTION profile or disabled component")
        if not 1_000 <= self.min_ms <= self.max_ms <= 60_000:
            raise ValueError("EVOLUTION duration must be 1–60 seconds with minimum no greater than maximum")
        if not 1 <= self.memory_mb <= 1024 or not 2 <= self.reasoning_calls <= 60 or not 10 <= self.reasoning_seconds <= 3600:
            raise ValueError("EVOLUTION memory/call/time budgets are out of range")
        if any(value is not None and not 1 <= value <= 12_000 for value in (self.max_frames, self.max_audio_windows)):
            raise ValueError("EVOLUTION frame and audio sample limits must be 1–12000")

    def fingerprint(self):
        from .artifacts import digest_file
        result = asdict(self)
        for field in ("embeddings", "audio_model", "model"):
            path = result[field]
            if path is not None:
                inputs = (path / "tokenizer.json", path / "onnx/model_quint8_avx2.onnx") if field == "embeddings" else (path,)
                result[field] = {"path": str(path.resolve()), "files": [digest_file(f) if f.is_file() else None for f in inputs]}
        return {"version": VERSION, **result}


def build_evidence(identity, transcript, turns, observations, asr, audio_stream, audio_hop):
    rows = [evidence_item(**identity, start_ms=s.start_ms, end_ms=s.end_ms, modality="utterance",
                          model_fingerprint=asr, provenance={"original": "transcript.json", "occurrence": index},
                          text=s.text, speaker_id=s.speaker_id, words=[asdict(w) for w in s.words],
                          timing="word" if s.words else "segment")
            for index, s in enumerate(transcript) if s.text.strip()]
    rows.extend(evidence_item(**identity, start_ms=t.start_ms, end_ms=t.end_ms, modality="speaker_turn",
                              model_fingerprint={"producer": "speaker-turn-sidecar/1"},
                              provenance={"original": "speaker_turns", "occurrence": index}, speaker_id=t.speaker_id)
                for index, t in enumerate(turns))
    for observation in observations:
        common = {"start_ms": observation.start_ms, "end_ms": observation.end_ms,
                  "model_fingerprint": {"producer": observation.kind, "audio_hop_ms": audio_hop},
                  "provenance": {"original": observation.modality}}
        if observation.modality == "audio":
            rows.append(evidence_item(**identity, **common, modality="acoustic_measurement", audio_stream=audio_stream,
                                      strength=observation.strength, text=observation.evidence))
        elif observation.modality == "chat":
            rows.append(evidence_item(**identity, **common, modality="chat_reaction",
                                      strength=observation.strength, text=observation.evidence))
    return rows


def segment_and_discover(memory: Memory, identity: dict, enabled: set[str], budget: int, maximum: int):
    utterances = memory.utterances
    tokens = {r["id"]: set(terms(r["text"])) for r in utterances}
    units = []
    candidates = {}

    def unit(group, level, method):
        children = tuple(r["id"] for r in group)
        start, end = min(r["start_ms"] for r in group), max(r["end_ms"] for r in group)
        row = evidence_item(**identity, start_ms=start, end_ms=end, modality="semantic_unit",
                            model_fingerprint={"producer": method}, provenance={"original": list(children)},
                            level=level, evidence_ids=list(children), text=" ".join(r["text"] for r in group)[:1600],
                            boundary_status="proposal")
        if row["id"] not in memory.rows:
            units.append(row)
            memory.rows[row["id"]] = row
        return children, start, end

    def add(proposal):
        key = proposal.core_ids or (f"{proposal.start_ms}:{proposal.end_ms}",)
        lanes = candidates.setdefault(key, {})
        if proposal.lane not in lanes or lanes[proposal.lane].strength < proposal.strength:
            lanes[proposal.lane] = proposal

    episode = []
    group_history = Counter()
    for i, row in enumerate(utterances):
        unit([row], 1, "asr-utterance")
        if episode:
            prior = episode[-1]
            topic_change = not tokens[row["id"]] & set().union(*(tokens[r["id"]] for r in episode[-3:]))
            if row["start_ms"] - prior["end_ms"] > (3500 if topic_change else 9000) or row["end_ms"] - episode[0]["start_ms"] > 90_000:
                unit(episode, 3, "topic-continuity")
                episode = []
        episode.append(row)
        group = []
        for neighbor in utterances[i:i + 4]:
            if group and (neighbor["start_ms"] - group[-1]["end_ms"] > 3200 or
                          neighbor["end_ms"] - row["start_ms"] > maximum):
                break
            group.append(neighbor)
            ids, start, end = unit(group, 2, "consecutive-thought")
            if "dialogue" not in enabled:
                continue
            text = " ".join(r["text"] for r in group)
            words = terms(text)
            if not words:
                continue
            distinct = set(words)
            density = len(distinct) / max(1, len(words))
            rarity = sum(1 / len(memory.postings[t]) ** .5 for t in distinct) / len(distinct)
            markers = len(PAYOFF.findall(text))
            strength = min(.98, min(.27, len(words) / 120) + .13 * density + .13 * rarity +
                           min(.25, markers * .14) + (.08 if text.rstrip().endswith((".", "!", "?")) else 0) +
                           (.10 if len(group) >= 2 else 0))
            signature = tuple(words)
            if group_history[signature] or ROUTINE.search(text):
                strength *= .55
            group_history[signature] += 1
            add(Proposal("dialogue", ids, start, end, round(strength, 4),
                         "Dialogue with a possible standalone idea, development or payoff"))
    if episode:
        unit(episode, 3, "topic-continuity")

    for row in list(memory.rows.values()):
        lane, strength, explanation = "", 0.0, ""
        if row["modality"] in {"motion", "scene_transition"}:
            lane, strength, explanation = "visual", row["strength"], "Independent visual change"
            if strength < .035:
                continue
        elif row["modality"] == "acoustic_event":
            lane, explanation = "acoustic", "YAMNet acoustic labels"
            routine = {"Speech", "Narration, monologue", "Conversation", "Music", "Inside, small room"}
            strength = max((x["confidence"] for x in row["labels"] if x["label"] not in routine), default=0)
            if strength < .32:
                continue
        elif row["modality"] == "acoustic_measurement":
            lane, strength, explanation = "acoustic", row["strength"] * .6, "Selected-track loudness or onset change"
        elif row["modality"] == "chat_reaction":
            lane, strength, explanation = "chat", row["strength"], "Source-clock chat reaction burst"
        if lane in enabled:
            start, end = row["start_ms"], row["end_ms"]
            if lane == "visual":
                start, end = max(0, start - 1800), min(identity["duration_ms"], end + 1800)
            add(Proposal(lane, (row["id"],), start, end, strength, explanation))

    if "exploration" in enabled:
        duration = identity["duration_ms"]
        step = max(maximum, ceil(duration / max(1, budget // 5)))
        for t in range(0, duration, step):
            stop = min(duration, t + maximum, t + 20_000)
            nearby = [r for r in memory.around(t, stop) if t <= r["start_ms"] < r["end_ms"] <= stop]
            if nearby:
                ids, start, end = unit(nearby, 2, "fixed-window-rescue")
            else:
                ids, start, end = (), t, stop
            add(Proposal("exploration", ids, start, end, .15, "Fixed-window coverage probe"))

    ranks = reciprocal_ranks({lane: sorted((key for key, value in candidates.items() if lane in value),
                                           key=lambda key: (-candidates[key][lane].strength, candidates[key][lane].start_ms))
                             for lane in LANES})
    order = sorted(candidates, key=lambda key: (-fused_score(ranks[key]),
                                                -max(p.strength for p in candidates[key].values())))
    reserved = []
    for lane in LANES:
        lane_top = next((key for key in order if ranks[key].get(lane) == 1), None)
        if lane_top is not None:
            reserved.append(lane_top)
    allotted = list(dict.fromkeys([*reserved, *order]))[:budget]
    return [replace(max(candidates[key].values(), key=lambda p: p.strength), ranks=ranks[key])
            for key in allotted], units


def run_evolution(config: EvolutionConfig, *, source: Path, source_id: str, duration: int, cache: Path,
                  transcript, turns, observations, audio, audio_stream: int, audio_hop: int,
                  asr: dict, provider, proposals_per_hour: int, limit: int, min_score: float):
    identity = {"source_id": source_id, "duration_ms": duration}
    enabled = set(LANES) - set(config.disabled)
    batch, interval, frame_cap, audio_cap = PROFILES[config.profile]
    rows = build_evidence(identity, transcript, turns, observations, asr, audio_stream, audio_hop)
    states = {"utterance": {"state": "available" if transcript else "unavailable", "count": len(transcript)},
              "speaker_turn": {"state": "available" if turns else "unavailable", "count": len(turns)},
              "chat": {"state": "available" if any(r["modality"] == "chat_reaction" for r in rows) else "unavailable"},
              "acoustic_heuristic": {"state": "available" if audio else "unavailable",
                                     "coverage_ms": min(duration, len(audio) * audio_hop)}}
    timings = {}
    for name, active, load in (
        ("semantic_audio", "acoustic" in enabled and config.audio_model,
         lambda: yamnet(source, config.audio_model, cache, identity, audio_stream, config.max_audio_windows or audio_cap)),
        ("visual", "visual" in enabled,
         lambda: visual_motion(source, cache, identity, interval, config.max_frames or frame_cap)),
    ):
        started = perf_counter()
        states[name] = {"state": "unavailable", "reason": "disabled_or_not_configured"}
        if active:
            try:
                evidence, states[name] = load()
                rows.extend(evidence)
            except (ImportError, OSError, TypeError, ValueError, RuntimeError) as exc:
                states[name] = {"state": "failed", "reason": str(exc)}
        timings[name + "_seconds"] = round(perf_counter() - started, 3)

    started = perf_counter()
    memory = Memory(rows, source_id, duration)
    timings["memory_index_seconds"] = round(perf_counter() - started, 3)
    states["dense"] = {"state": "unavailable", "reason": "disabled_or_not_configured"}
    started = perf_counter()
    if config.embeddings and "memory" not in config.disabled and "hybrid" not in config.disabled:
        try:
            states["dense"] = memory.enable_dense(config.embeddings, cache, batch=batch, max_mb=config.memory_mb)
        except (ImportError, OSError, TypeError, ValueError, RuntimeError) as exc:
            states["dense"] = {"state": "failed", "reason": str(exc)}
    timings["embedding_seconds"] = round(perf_counter() - started, 3)
    started = perf_counter()
    proposals, units = segment_and_discover(memory, identity, enabled,
                                            min(240, max(8, ceil(duration * proposals_per_hour / 3_600_000))), config.max_ms)
    timings["discovery_seconds"] = round(perf_counter() - started, 3)
    reasoner = Reasoner(provider, cache, config.reasoning_calls, config.reasoning_seconds) if provider and limit else None
    interest = None
    if config.model:
        from .evolution_feedback import load_model
        interest = load_model(config.model)
    records = []
    started = perf_counter()
    for proposal in proposals:
        candidate_id = artifact_key([VERSION, source_id, proposal.lane, proposal.core_ids, proposal.start_ms, proposal.end_ms])[:24]
        analysis = investigate(memory, proposal, "memory" not in config.disabled, "hybrid" not in config.disabled)
        decision = _assess(memory, proposal, candidate_id, analysis, reasoner, source, cache, config, identity,
                           audio, audio_hop, min_score, interest)
        records.append(decision)
    timings["investigation_seconds"] = round(perf_counter() - started, 3)
    selected = []
    for candidate, row in sorted(records, key=lambda item: -item[0].score):
        if row["decision"] != "eligible":
            continue
        core = set(row["proposal"]["core_ids"])
        if any(core & set(other["proposal"]["core_ids"]) or
               repeated_story(candidate.transcript_excerpt, c.transcript_excerpt) or
               (candidate.start_ms, candidate.end_ms) == (c.start_ms, c.end_ms) for c, other in selected):
            row["decision"] = "duplicate"
        elif len(selected) >= limit:
            row["decision"] = "review_limit"
        else:
            row["decision"] = "selected"
            selected.append((candidate, row))
    report = {"schema_version": "chomp-evolution-evidence/1", **identity, "availability": states,
              "observations": list(memory.rows.values()), "semantic_unit_count": len(units),
              "retrieval_history": memory.history, "timing": timings,
              "reasoning": {"attempts": reasoner.attempts, "cache_hits": reasoner.cache_hits, "errors": reasoner.errors,
                            "provider": reasoner.fingerprint} if reasoner else {"state": "local_review"},
              "interest_model": interest["model_id"] if interest else None}
    return records, [candidate for candidate, _ in selected], report


def _assess(memory, proposal, candidate_id, analysis, reasoner, source, cache, config, identity,
            audio, audio_hop, min_score, interest):
    duration = identity["duration_ms"]
    core = memory.hydrate(proposal.core_ids)
    dialogue = " ".join(r["text"] for r in core if r["modality"] == "utterance")
    score = min(100., round(proposal.strength * 100, 2))
    state = "unresolved"
    note = "Local evidence needs editorial review"
    method = "evolution_local_review"
    boundary = model_data = final = None
    alternatives = []
    frames = []
    groups = analysis["viewer_required_groups"]
    features = [proposal.strength, min(len(terms(dialogue)) / 60, 1),
                min(len(PAYOFF.findall(dialogue)) / 3, 1), float(bool(analysis["risks"])),
                *(float(proposal.lane == lane) for lane in LANES)]
    if interest:
        from .evolution_feedback import interest_score
        score = round((score + 100 * interest_score(interest, features)) / 2, 2)
    if reasoner and not proposal.core_ids:
        note = "Exploration interval has no grounded evidence"
    elif reasoner:
        try:
            if proposal.lane == "visual":
                step = (proposal.end_ms - proposal.start_ms) // 4
                frames = visual_frames(source, cache, identity,
                                       [proposal.start_ms + step * i for i in (0, 1, 2, 3)], proposal.end_ms)
                memory.rows.update({row["id"]: row for row, _ in frames})
            model_data = provider_investigation(reasoner, memory, proposal, candidate_id, analysis, frames,
                                                hybrid="hybrid" not in config.disabled,
                                                allow_search="memory" not in config.disabled, duration=duration)
            answer = model_data["answer"]
            note = answer["viewer_value"] or answer["stop_reason"]
            state = {"select": "verify", "reject": "rejected", "unresolved": "unresolved"}[answer["action"]]
            if answer["critical_issues"] or answer["uncertainty"] == "high":
                if state == "verify":
                    state = "unresolved"
            model_required = [r["evidence_id"] for r in answer["relationships"] if r["role"] == "viewer"]
            if set(model_required) - set(x for group in answer["viewer_groups"] for x in group):
                raise ValueError("Provider viewer relationships lack a dependency group")
            groups = [list(dict.fromkeys([*groups[0], *group])) for group in answer["viewer_groups"]]
        except (InvestigationBudget, AttributeError, OSError, TypeError, KeyError, ValueError, RuntimeError) as exc:
            state, note = "unresolved", str(exc)
    elif dialogue and proposal.lane in {"dialogue", "exploration"}:
        if not analysis["risks"] and "local-review" not in config.disabled and proposal.strength >= .58:
            state, note = "eligible", "Provisional dialogue selection based on structure and source-wide context checks"
        elif proposal.strength < .43:
            state, note = "rejected", "Local discovery found weak evidence of a standalone moment"

    if state in {"eligible", "verify"}:
        boundary, alternatives = close(memory, proposal, groups, duration=duration, minimum=min(duration, config.min_ms),
                                       maximum=config.max_ms, audio=audio, hop=audio_hop, snap="snap" not in config.disabled)
        if boundary is None:
            state, note = "unresolved", "No contiguous interval covers the core and required viewer context"
    if state == "verify":
        try:
            in_cut = [r for r in core if boundary.start_ms <= r["start_ms"] and r["end_ms"] <= boundary.end_ms]
            in_cut += memory.around(boundary.start_ms, boundary.end_ms)
            cut_frames = [(r, pixels) for r, pixels in frames if boundary.start_ms <= r["start_ms"] < boundary.end_ms]
            in_cut += [r for r, _ in cut_frames]
            extra = [*model_data["additional_ids"], *analysis["interpretation_only_ids"],
                     *model_data["answer"]["interpretation_ids"], *model_data["answer"]["supporting_ids"]]
            in_range = {r["id"]: r for r in in_cut}
            payload = {"candidate_id": candidate_id, "candidate_core_ids": list(proposal.core_ids),
                       "interval_ms": [boundary.start_ms, boundary.end_ms], "lane": proposal.lane,
                       "ordered_image_ids": [r["id"] for r, _ in cut_frames],
                       "interpretation_only_ids": [item for item in dict.fromkeys(extra) if item not in in_range],
                       "investigation": model_data["answer"],
                       "evidence": evidence_view([*in_range.values(), *memory.hydrate(dict.fromkeys(
                           item for item in extra if item not in in_range))])}
            final = reasoner.call("final", payload, [pixels for _, pixels in cut_frames])
            citations = set(final["supporting_ids"])
            if final["action"] == "select":
                if not citations <= in_range.keys():
                    raise ValueError("Final selection cites evidence outside the extraction")
                if not citations.intersection(proposal.core_ids) and proposal.lane != "visual":
                    raise ValueError("Final selection does not cite the interesting core")
                if proposal.lane == "visual" and len(citations & set(payload["ordered_image_ids"])) < 2:
                    raise ValueError("Visual selection needs at least two in-range source frames")
            state = {"select": "eligible", "reject": "rejected", "unresolved": "unresolved"}[final["action"]]
            score, note, method = final["score"], final["rationale"], "evolution_director_verified"
        except (InvestigationBudget, AttributeError, OSError, TypeError, KeyError, ValueError, RuntimeError) as exc:
            state, note = "unresolved", str(exc)

    if state == "eligible" and score < min_score:
        state, note = "rejected", f"Below the configured score threshold ({score:g} < {min_score:g})"
    if boundary:
        start, end = boundary.start_ms, boundary.end_ms
    else:
        start, end = proposal.start_ms, proposal.end_ms
    cut_text = " ".join(row["text"] for row in memory.around(start, end)
                        if start <= row["start_ms"] and row["end_ms"] <= end)
    candidate = Candidate(start, end, proposal.anchor, transcript_excerpt=cut_text[:2200], score=score,
                          ranking_method=method, rationale=note, missing_context=state != "eligible",
                          requires_visual_verification=proposal.lane != "visual" or state != "eligible")
    candidate.validate(duration)
    row = {"candidate_id": candidate_id, "proposal": asdict(proposal), "candidate": candidate.to_dict(),
           "boundary": asdict(boundary) if boundary else None, "boundary_alternatives": alternatives,
           "decision": state, "reason_detail": note, "investigation": analysis, "provider_investigation": model_data,
           "judgment": final, "feature_vector": features}
    return candidate, row
