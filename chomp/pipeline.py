"""Local VOD analysis, clip decisions, and review export."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from shutil import disk_usage
from tempfile import TemporaryDirectory
from time import perf_counter

from .artifacts import artifact_key, artifact_lock, digest_file, source_identity, write_json
from .asr import LocalTranscriber
from .boundaries import refine_boundaries
from .clipwork import PROFILES, SCHEMA, create_clip, read_layout_plan, review_page
from .discovery import discover
from .editorial import EditorialProvider, context_for, judge_cached, story_boundary
from .evolution import EvolutionConfig, run_evolution
from .framing import facecam_fingerprint, plan_framing
from .media import RENDER_VERSION, check_audio_track, probe, scan_audio
from .ranking import duplicate, heuristic_rank, quality, select
from .reframe import cached_shorts_plan, vision_fingerprint
from .signals import audio_observations, chat_observations, final_evidence, nearby_text, read_chat, read_transcript, transcript_observations
from .speakers import attribute_segments, candidate_speaker_context, read_speaker_turns, speaker_inventory

_PIPELINE_VERSION = "stage1/0.5"
_AUDIO_HOP_MS = 250


def transcribe_media(media: Path, model: str = "small", device: str = "cpu", *,
                     audio_stream: int = 0, words: bool = False) -> list[dict]:
    meta = probe(media)
    check_audio_track(meta, audio_stream)
    return LocalTranscriber(model, device).transcribe(media, 0, meta["duration_ms"], audio_stream, words=words)


def _job_status(database: sqlite3.Connection, job_id: str, source: Path, manifest: Path, status: str) -> None:
    database.execute(
        "INSERT INTO jobs(job_id,source,status,modified_utc,manifest) VALUES(?,?,?,?,?) "
        "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, modified_utc=excluded.modified_utc",
        (job_id, str(source), status, datetime.now(timezone.utc).isoformat(), str(manifest)),
    )
    database.commit()


def _audio_curve(cache: Path, source: Path, source_id: str, audio_stream: int, duration_ms: int, *,
                 start_ms: int = 0) -> tuple[list[float], bool]:
    fingerprint = {"source_id": source_id, "audio_stream": audio_stream, "duration_ms": duration_ms,
                   "hop_ms": _AUDIO_HOP_MS, "analysis_version": 2}
    if start_ms:
        fingerprint["start_ms"] = start_ms
    key = artifact_key(fingerprint)
    path = cache / "audio" / f"{key}.json"
    with artifact_lock(cache / "locks" / f"{key}.lock", timeout=300):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))["levels"], True
        region = {"start_ms": start_ms} if start_ms else {}
        levels = scan_audio(source, audio_stream=audio_stream, hop_ms=_AUDIO_HOP_MS, duration_ms=duration_ms, **region)
        write_json(path, {"provenance": fingerprint, "levels": levels})
        return levels, False


def process(
    media: Path, output_root: Path, *, rights_ref: str, transcript_path: Path | None = None,
    chat_path: Path | None = None, chat_offset_ms: int = 0, speaker_turns_path: Path | None = None,
    main_speaker_id: str | None = None, transcribe: bool = False, asr_model: str = "small",
    asr_device: str = "cpu", asr_language: str | None = None, min_score: float = 45, limit: int = 3,
    render: bool = False, facecam_roi: tuple[int, int, int, int] | None = None,
    gameplay_roi: tuple[int, int, int, int] | None = None, layout: str = "auto",
    audio_stream: int = 0, facecam_model: Path | None = None, layout_plan: Path | None = None,
    refine_captions: bool = False, caption_position: str = "lower", profile: str = "preview",
    audio_profile: str = "original", reaction_lag_ms: int = 0, proposals_per_hour: int = 40,
    editorial_provider: EditorialProvider | None = None, semantic_budget: int = 12,
    saliency_model: Path | None = None, camera_slot: str = "top", person_model: Path | None = None,
    camera_motion: str = "pan", camera_fit: str = "auto",
    burn_hook: bool = False, evolution: EvolutionConfig | None = None, evolution_provider=None,
) -> dict:
    started = perf_counter()
    if not rights_ref.strip():
        raise ValueError("A nonempty rights reference is required")
    if type(limit) is not int or not 0 <= limit <= 3 or not 0 <= min_score <= 100:
        raise ValueError("limit must be 0..3 and score threshold 0..100")
    if not 1 <= semantic_budget <= 120 or type(semantic_budget) is not int:
        raise ValueError("Semantic budget must be an integer between 1 and 120")
    if profile not in PROFILES:
        raise ValueError("Unknown render profile")

    if evolution is not None and (type(proposals_per_hour) is not int or not 1 <= proposals_per_hour <= 240):
        raise ValueError("EVOLUTION proposals per hour must be 1–240")
    media = media.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    meta = probe(media)
    duration_ms = meta["duration_ms"]
    check_audio_track(meta, audio_stream)
    source_id = source_identity(media)
    source_stat = media.stat() if evolution is not None else None
    cache = output_root / ".analysis"
    internal_asr = transcribe and transcript_path is None
    caption_asr = render and limit > 0 and (refine_captions or internal_asr)
    quiet_gain = evolution is not None and "quiet-asr" not in evolution.disabled
    asr_options = {"quiet_gain": True} if quiet_gain else {}
    transcriber = LocalTranscriber(asr_model, asr_device, asr_language, **asr_options) if internal_asr or caption_asr else None
    provider = editorial_provider if limit else None

    asr_error = None
    try:
        asr_signature = transcriber.fingerprint if transcriber else None
    except (OSError, ValueError, RuntimeError) as exc:
        if evolution is None:
            raise
        asr_signature = {"requested": asr_model, "device": asr_device, "unavailable": str(exc)}
        asr_error = str(exc)
        transcriber = None

    config = {
        "source": str(media), "source_id": source_id, "pipeline_version": _PIPELINE_VERSION,
        "render_version": RENDER_VERSION,
        "rights_ref": rights_ref, "transcript": digest_file(transcript_path) if transcript_path else None,
        "chat": digest_file(chat_path) if chat_path else None, "chat_offset_ms": chat_offset_ms,
        "reaction_lag_ms": reaction_lag_ms, "speaker_turns": digest_file(speaker_turns_path) if speaker_turns_path else None,
        "main_speaker_id": main_speaker_id, "audio_stream": audio_stream, "asr": asr_signature,
        "internal_asr": internal_asr, "caption_asr": caption_asr, "proposals_per_hour": proposals_per_hour,
        "min_score": min_score, "limit": limit, "render": render, "layout": layout,
        "facecam_roi": facecam_roi, "gameplay_roi": gameplay_roi, "profile": profile,
        "layout_plan": digest_file(layout_plan) if layout_plan else None,
        "caption_position": caption_position, "audio_profile": audio_profile,
        "editorial_provider": provider.fingerprint if provider else None, "semantic_budget": semantic_budget,
        "camera_slot": camera_slot, "burn_hook": burn_hook,
    }
    if evolution is not None:
        config["evolution"] = evolution.fingerprint()
        config["evolution_provider"] = evolution_provider.fingerprint if evolution_provider and limit else None
    if layout == "shorts" and facecam_roi is None and layout_plan is None:
        config["vision"] = vision_fingerprint(facecam_model, saliency_model, camera_motion=camera_motion,
                                              camera_fit=camera_fit, person_model=person_model)
    elif layout == "auto-facecam" and facecam_roi is None and layout_plan is None:
        config["facecam_detector"] = facecam_fingerprint(facecam_model)
    job_id = artifact_key(config)[:24]
    job_dir = output_root / job_id
    manifest_path = job_dir / "review_queue.json"
    job_lock = output_root / ".locks" / f"{job_id}.lock"
    with artifact_lock(job_lock, timeout=5):
        if manifest_path.is_file():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if evolution is not None:
                if artifact_key(existing["provenance"]) != artifact_key(config):
                    raise ValueError("EVOLUTION review metadata does not match this job")
                hashes = existing["evolution_artifact_sha256"]
                if not {"candidates.json", "evolution_evidence.json", "review.html"} <= hashes.keys():
                    raise ValueError("EVOLUTION review is missing its integrity metadata")
                for clip in existing["clips"]:
                    hashes = {**hashes, **{Path(clip[key]).name: value for key, value in clip["artifact_sha256"].items()
                                         if value and clip.get(key)}}
                if any(Path(name).name != name or not (job_dir / name).is_file()
                       or digest_file(job_dir / name) != expected
                       for name, expected in hashes.items()):
                    raise ValueError(f"EVOLUTION review artifacts changed. Use a new output folder to rerun {job_id}.")
            return {**existing, "reused_job": True}

        if evolution is not None and disk_usage(output_root).free < evolution.min_free_mb * 1024**2:
            raise ValueError("EVOLUTION output disk is below the configured free-space minimum")

        with closing(sqlite3.connect(output_root / "jobs.sqlite", timeout=20)) as database:
            database.execute("CREATE TABLE IF NOT EXISTS jobs "
                             "(job_id TEXT PRIMARY KEY, source TEXT, status TEXT, modified_utc TEXT, manifest TEXT)")
            _job_status(database, job_id, media, manifest_path, "running")
            try:
                asr_started = perf_counter()
                diagnostics_asr = asr_signature
                if transcript_path:
                    transcript = read_transcript(transcript_path, duration_ms)
                elif internal_asr and transcriber:
                    try:
                        transcript, _ = transcriber.cached(cache, media, source_id, duration_ms, audio_stream,
                                                           **({"words": True} if evolution is not None else {}))
                    except (OSError, ValueError, RuntimeError) as exc:
                        if evolution is None:
                            raise
                        asr_error = str(exc)
                        transcript = []
                        transcriber = None
                        if asr_device == "cuda":
                            try:
                                cpu = LocalTranscriber(asr_model, "cpu", asr_language, **asr_options)
                                transcript, _ = cpu.cached(cache, media, source_id, duration_ms, audio_stream, words=True)
                                diagnostics_asr = cpu.fingerprint
                                transcriber = cpu
                            except (OSError, ValueError, RuntimeError) as cpu_error:
                                asr_error = f"{asr_error}; CPU fallback: {cpu_error}"
                else:
                    transcript = []
                turns = read_speaker_turns(speaker_turns_path, duration_ms, source_id=source_id, audio_stream=audio_stream)
                transcript = attribute_segments(transcript, turns)
                speaker_ids = speaker_inventory(transcript, turns, main_speaker_id)
                chat = read_chat(chat_path, duration_ms, chat_offset_ms)
                diagnostics = {"transcript_seconds": round(perf_counter() - asr_started, 3)}
                if evolution is not None and internal_asr and transcriber:
                    diagnostics["asr_cache_hit"] = transcriber.last_cache_hit

                analysis_started = perf_counter()
                audio_error = None
                try:
                    audio, audio_cached = _audio_curve(cache, media, source_id, audio_stream, duration_ms)
                except (OSError, ValueError, RuntimeError) as exc:
                    if evolution is None:
                        raise
                    audio_error = str(exc)
                    audio, audio_cached = [], False
                observations = audio_observations(audio, duration_ms, hop_ms=_AUDIO_HOP_MS)
                observations += transcript_observations(transcript, duration_ms)
                observations += chat_observations(chat, duration_ms, reaction_lag_ms=reaction_lag_ms)
                evolution_report = None
                if evolution is not None:
                    diagnostics.update(audio_cache_hit=audio_cached,
                                       audio_scan_seconds=round(perf_counter() - analysis_started, 3))
                    records, selected, evolution_report = run_evolution(
                        evolution, source=media, source_id=source_id, duration=duration_ms, cache=cache,
                        transcript=transcript, turns=turns, observations=observations, audio=audio,
                        audio_stream=audio_stream, audio_hop=_AUDIO_HOP_MS,
                        asr={"producer": "transcript-sidecar/1", "sha256": config["transcript"]} if transcript_path
                            else diagnostics_asr or {"producer": "missing-asr"},
                        provider=evolution_provider, proposals_per_hour=proposals_per_hour,
                        limit=limit, min_score=min_score,
                    )
                    diagnostics.update(evolution_report["timing"])
                    if asr_error:
                        evolution_report["availability"]["utterance"] = {
                            "state": "available" if transcriber else "failed", "reason": asr_error,
                            "fallback": "cpu" if transcriber else None, "count": len(transcript),
                        }
                    if audio_error:
                        evolution_report["availability"]["acoustic_heuristic"] = {"state": "failed", "reason": audio_error}
                    candidates = [candidate for candidate, _ in records]
                    by_object = {id(candidate): row for candidate, row in records}
                    for candidate, row in records:
                        _, contained = final_evidence(observations, transcript, candidate.start_ms, candidate.end_ms)
                        candidate.speaker_context = candidate_speaker_context(
                            contained, candidate.start_ms, candidate.end_ms, main_speaker_id)
                        row["candidate"]["speaker_context"] = candidate.speaker_context
                else:
                    candidates = discover(observations, transcript, duration_ms, proposals_per_hour=proposals_per_hour)
                    diagnostics.update(audio_cache_hit=audio_cached, discovery_seconds=round(perf_counter() - analysis_started, 3))

                    ranking_started = perf_counter()
                    records = []
                    eligible = []
                    judgments_sent = judgments_cached = 0
                    for index, candidate in enumerate(candidates):
                        original = candidate.to_dict()
                        identifier = artifact_key([source_id, original["start_ms"], original["end_ms"],
                                                   candidate.anchor_ms, original["observations"]])[:24]
                        decision = refine_boundaries(candidate, transcript, duration_ms, audio_levels=audio,
                                                     audio_hop_ms=_AUDIO_HOP_MS,
                                                     max_length_ms=26_000 if layout == "shorts" else 60_000)
                        judgment = context = None
                        reason = "eligible"
                        error_detail = None
                        if provider is not None:
                            context = context_for(candidate, transcript, duration_ms)
                            if not context["utterances"]:
                                reason = "insufficient_transcript_for_semantic_judgment"
                            elif index >= semantic_budget:
                                reason = "semantic_budget_exhausted"
                            else:
                                try:
                                    judgment, reused = judge_cached(provider, context, cache)
                                    judgments_cached += int(reused)
                                    judgments_sent += int(not reused)
                                    if judgment["verdict"] == "select":
                                        decision = story_boundary(candidate, judgment, context, duration_ms)
                                        candidate.start_ms, candidate.end_ms = decision.start_ms, decision.end_ms
                                    else:
                                        reason = judgment["verdict"]
                                except (ValueError, RuntimeError, TimeoutError) as exc:
                                    reason = "semantic_abstention"
                                    error_detail = str(exc)
                        c0, c1 = candidate.start_ms, candidate.end_ms
                        candidate.observations, contained = final_evidence(observations, transcript, c0, c1)
                        candidate.transcript_excerpt = nearby_text(contained, c0, c1)
                        candidate.speaker_context = candidate_speaker_context(contained, c0, c1, main_speaker_id)
                        if judgment is not None and reason == "eligible":
                            candidate.categories = judgment["categories"] or ["other"]
                            candidate.scores = judgment["scores"]
                            candidate.score = quality(candidate.scores)
                            candidate.ranking_method = "grounded_semantic"
                            candidate.rationale = judgment["rationale"]
                            candidate.missing_context = False
                            candidate.requires_visual_verification = judgment["requires_visual_verification"]
                        else:
                            heuristic_rank(candidate)
                        candidate.validate(duration_ms)
                        if reason == "eligible":
                            if candidate.score < min_score:
                                reason = "below_threshold"
                            else:
                                eligible.append(candidate)
                        records.append((candidate, {"candidate_id": identifier, "proposal": original,
                                                    "candidate": candidate.to_dict(), "boundary": asdict(decision),
                                                    "judgment": judgment, "judgment_context": context,
                                                    "decision": reason, "reason_detail": error_detail}))
                    selected = select(eligible, max_clips=limit, min_score=min_score)
                    selected_ids = {id(candidate) for candidate in selected}
                    by_object = {}
                    for candidate, row in records:
                        if id(candidate) in selected_ids:
                            row["decision"] = "selected"
                        elif row["decision"] == "eligible":
                            row["decision"] = ("duplicate" if any(duplicate(candidate, c) for c in selected)
                                               else "review_limit")
                        by_object[id(candidate)] = row
                    diagnostics.update(ranking_seconds=round(perf_counter() - ranking_started, 3),
                                       local_model_judgments=judgments_sent, cached_judgments=judgments_cached,
                                       remote_api_cost=0)
                overrides = read_layout_plan(layout_plan)
                render_started = perf_counter()
                with TemporaryDirectory(prefix=".job-", dir=output_root, ignore_cleanup_errors=True) as temporary:
                    work = Path(temporary)
                    snapshot = job_dir / "transcript.json" if transcript else None
                    transcript_hash = None
                    if snapshot:
                        write_json(work / snapshot.name, {"segments": [asdict(segment) for segment in transcript]})
                        transcript_hash = digest_file(work / snapshot.name)

                    assets = []
                    for i, candidate in enumerate(selected, 1):
                        record = by_object[id(candidate)]
                        clip_work = work
                        if evolution is not None:
                            clip_work = work / f".candidate-{i:02d}"
                            clip_work.mkdir()
                        try:
                            title = (record["judgment"] or {}).get("title")
                            start_ms, end_ms = candidate.start_ms, candidate.end_ms
                            if "vision" in config:
                                framing = cached_shorts_plan(media, source_id, start_ms, end_ms, cache,
                                                             face_model=facecam_model, saliency_model=saliency_model,
                                                             camera_slot=camera_slot, fingerprint=config["vision"],
                                                             person_model=person_model)
                            elif overrides is None and layout == "shorts":
                                framing = plan_framing(media, start_ms, end_ms, layout, facecam_roi, facecam_model,
                                                       camera_slot=camera_slot)
                            else:
                                framing = plan_framing(media, start_ms, end_ms, layout if overrides is None else "fit",
                                                       facecam_roi if overrides is None else None, facecam_model)
                            clip_text = transcript
                            caption_source, caption_source_hash = snapshot, transcript_hash
                            if caption_asr and transcriber and (evolution is None or not internal_asr):
                                try:
                                    refined, refined_path = transcriber.cached(
                                        cache, media, source_id, duration_ms, audio_stream,
                                        start_ms=max(0, start_ms - 3000), end_ms=min(duration_ms, end_ms + 3000), words=True,
                                    )
                                    if any(s.start_ms < end_ms and s.end_ms > start_ms for s in refined):
                                        clip_text = refined
                                        caption_source, caption_source_hash = refined_path, digest_file(refined_path)
                                except (OSError, ValueError, RuntimeError) as exc:
                                    if evolution is None:
                                        raise
                                    record["caption_refinement_error"] = str(exc)
                            clip = create_clip(
                                clip_work, job_dir, f"candidate_{i:02d}", source=media, source_id=source_id,
                                rights_ref=rights_ref, start_ms=start_ms, end_ms=end_ms, duration_ms=duration_ms,
                                transcript=clip_text, transcript_path=caption_source, transcript_sha256=caption_source_hash,
                                framing=framing, gameplay_roi=gameplay_roi, audio_stream=audio_stream, render=render,
                                profile=profile, caption_position=caption_position, audio_profile=audio_profile,
                                layout_override=overrides,
                                shorts_style=layout == "shorts",
                                title=title, title_source="cited_dialogue", burn_hook=burn_hook,
                            )
                        except (OSError, ValueError, RuntimeError) as exc:
                            if evolution is None:
                                raise
                            record.update(decision="processing_failure", render_error=str(exc))
                            for item in clip_work.iterdir():
                                item.unlink()
                            clip_work.rmdir()
                            continue
                        if evolution is not None:
                            for item in clip_work.iterdir():
                                item.replace(work / item.name)
                            clip_work.rmdir()
                        assets.append({**clip, "candidate_id": record["candidate_id"],
                                       "candidate": record["candidate"], "boundary": record["boundary"]})

                    if source_stat is not None:
                        from .evolution_hardware import peak_process_rss
                        diagnostics["peak_worker_rss_bytes"] = peak_process_rss()
                        current = media.stat()
                        if (current.st_size, current.st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns):
                            raise ValueError("Source changed during EVOLUTION analysis or rendering")
                        diagnostics["processing_failures"] = sum(row["decision"] == "processing_failure"
                                                                  for _, row in records)
                    diagnostics.update(export_seconds=round(perf_counter() - render_started, 3),
                                       total_seconds=round(perf_counter() - started, 3))
                    diagnostics["seconds_per_source_hour"] = round(diagnostics["total_seconds"] * 3_600_000 / duration_ms, 2)
                    ledger = {"schema_version": "chomp-candidates/1", "job_id": job_id, "source_id": source_id,
                              "source": str(media), "duration_ms": duration_ms, "proposals_per_hour": proposals_per_hour,
                              "candidates": [row for _, row in records], "timing": diagnostics}
                    write_json(work / "candidates.json", ledger)
                    if evolution_report is not None:
                        write_json(work / "evolution_evidence.json", evolution_report)
                    manifest = {
                        "schema_version": SCHEMA, "job_id": job_id, "source": str(media), "source_id": source_id,
                        "duration_ms": duration_ms, "rights_ref": rights_ref,
                        "chat_status": "available" if chat else "missing",
                        "transcript_status": "available" if transcript else "missing",
                        "speaker_ids": speaker_ids, "main_speaker_id": main_speaker_id,
                        "speaker_attribution_status": ("turns_imported" if turns else
                                                       "transcript_labelled" if speaker_ids else "unavailable"),
                        "ranking_notice": ("Local structured judgments require human verification." if provider else
                                           "Provisional heuristic ranking. Human content review required."),
                        "candidate_ledger": str(job_dir / "candidates.json"),
                        "review_page": str(job_dir / "review.html"), "candidate_count": len(candidates),
                        "selected_for_review": len(assets), "warnings": [w for clip in assets for w in clip["warnings"]],
                        "clips": assets, "manual_review_required": True, "auto_publishing": False,
                        "timing": diagnostics, "provenance": config,
                    }
                    review_page(work / "review.html", assets, "Chomp-Chomp clip review")
                    if evolution_report is not None:
                        manifest.update(engine="evolution", evolution_evidence=str(job_dir / "evolution_evidence.json"),
                            ranking_notice="EVOLUTION review. Check source meaning and technical quality before approval.",
                            analysis_status="partial" if diagnostics["processing_failures"] else "complete",
                            evolution_artifact_sha256={item.name: digest_file(item) for item in work.iterdir()
                                                       if item.name in {"candidates.json", "evolution_evidence.json",
                                                                        "review.html", "transcript.json"}})
                    write_json(work / manifest_path.name, manifest)
                    if job_dir.exists():
                        raise FileExistsError(f"An incomplete job already exists at {job_dir}. Choose a new output folder.")
                    work.rename(job_dir)
                _job_status(database, job_id, media, manifest_path, "review_pending")
                return manifest
            except BaseException:
                _job_status(database, job_id, media, manifest_path, "failed")
                raise
