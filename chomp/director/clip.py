"""Terminal-first Director jobs and their review artifacts."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from html import escape
import json
from pathlib import Path
import sys
from time import perf_counter

from ..artifacts import artifact_key, artifact_lock, digest_file, source_identity, write_json, write_text
from ..asr import LocalTranscriber
from ..boundaries import refine_boundaries
from ..discovery import discover
from ..media import check_audio_track, probe
from ..models import checked_span
from ..pipeline import _AUDIO_HOP_MS, _audio_curve
from ..ranking import heuristic_rank
from ..signals import audio_observations, chat_observations, final_evidence, nearby_text, read_chat, read_transcript, transcript_observations
from .campaign import Campaign, milliseconds, resolve_campaign
from .provider import CodexCLI
from .selection import TranscriptIndex
from .semantic import (BudgetPolicy, DirectorFailure, EditorialCalls, SelectionProvider, STRIDE_MS, WINDOW_MS,
                       enforce, investigate, scan_dialogue)

JOB_CONTRACT = "director-selection/1"


def signal_candidates(observations, section):
    start, end = section
    local = [replace(row, start_ms=max(start, row.start_ms) - start, end_ms=min(end, row.end_ms) - start)
             for row in observations if row.start_ms < end and row.end_ms > start]
    candidates = discover(local, [], end - start)
    for candidate in candidates:
        candidate.start_ms += start
        candidate.end_ms += start
        candidate.anchor_ms += start
        candidate.observations = [replace(row, start_ms=row.start_ms + start, end_ms=row.end_ms + start)
                                  for row in candidate.observations]
    return candidates


def merge_signals(proposals: list[dict], signals, index: TranscriptIndex) -> None:
    for i, signal in enumerate(signals, 1):
        matched = next((row for row in proposals if "semantic" in row["lanes"]
                        and any(index.by_id[uid].start_ms <= signal.anchor_ms < index.by_id[uid].end_ms
                                for uid in row["evidence_ids"])), None)
        refs = [{"id": f"s{i}.{j}", **asdict(obs)} for j, obs in enumerate(signal.observations, 1)]
        if matched is not None:
            if "signal" not in matched["lanes"]:
                matched["lanes"].append("signal")
            matched["signals"].extend(refs)
        else:
            proposals.append({"candidate_id": f"s{i}", "lanes": ["signal"], "range_ms": [signal.start_ms, signal.end_ms],
                              "event": None, "viewer_value": None, "evidence_ids": [], "signals": refs})


def baseline_decisions(proposals, signals, index, transcript, observations, audio, section, duration_ms, campaign):
    minimum, _, maximum = campaign.durations_ms
    ordered = []
    for row, candidate in zip(proposals, signals, strict=True):
        boundary = refine_boundaries(candidate, transcript, duration_ms, min_length_ms=minimum, max_length_ms=maximum,
                                     audio_levels=audio if section[0] == 0 else None, audio_hop_ms=_AUDIO_HOP_MS)
        left, right = max(section[0], boundary.start_ms), min(section[1], boundary.end_ms)
        candidate.observations, contained = final_evidence(observations, transcript, left, right)
        candidate.transcript_excerpt = nearby_text(contained, left, right)
        heuristic_rank(candidate)
        evidence_ids = index.ids_in(left, right)
        # Apply the same speech-edge protection as the semantic mode.
        if evidence_ids:
            start, end = index.containing_span(evidence_ids)
            left, right = min(left, start), max(right, end)
        ordered.append({"candidate_id": row["candidate_id"], "verdict": "select" if candidate.score >= 45 else "reject",
                        "reason": candidate.rationale if candidate.score >= 45 else "Below the baseline signal threshold.",
                        "event": None, "viewer_value": None, "audience": None, "campaign_fit": None, "confidence": None,
                        "setup_ids": [], "development_ids": [], "payoff_ids": [], "evidence_ids": evidence_ids,
                        "title_suggestion": None, "hook_suggestion": None, "heuristic_range_ms": [left, right],
                        "boundary_note": boundary.note, "heuristic_scores": candidate.scores,
                        "heuristic_score": candidate.score})
    return sorted(ordered, key=lambda row: -row["heuristic_score"])


def review_html(report: dict, media: Path, *, include_rejected: bool) -> str:
    def safe(value) -> str:
        return escape(str(value or ""), quote=True)

    def seconds(ms: int) -> str:
        return f"{ms / 1000:.3f}"

    cards = []
    for decision in report["decisions"]:
        selected = decision["verdict"] == "select"
        if not selected and not include_rejected:
            continue
        boundary = decision.get("boundary")
        interval = preview = ""
        if boundary:
            left, right = seconds(boundary["start_ms"]), seconds(boundary["end_ms"])
            interval = f"<p>Source interval {left}–{right} s. {safe(boundary['note'])}</p>"
            if selected:
                preview = f'<video controls preload="none" src="{safe(media.as_uri())}#t={left},{right}"></video>'
        evidence = "".join(f"<li>{safe(row['id'])} at {seconds(row['start_ms'])} s. {safe(row['text'])}</li>"
                           for row in decision["evidence"])
        evidence = f"<details><summary>Transcript evidence</summary><ul>{evidence}</ul></details>" if evidence else ""
        notes = "".join(f"<p>{label} {safe(decision[field])}</p>" for field, label in (
            ("event", "Moment"), ("viewer_value", "Why a viewer might care"), ("audience", "Intended audience"),
            ("campaign_fit", "Editorial campaign assessment"), ("confidence", "Editorial confidence"),
            ("title_suggestion", "Suggested title"), ("hook_suggestion", "Suggested opening"),
            ("constraint_violation", "Validation result")) if decision.get(field))
        warnings = ("<p>Source preview. Visual framing, sentence edges, and caption timing require human review. "
                    "The proposed cut passed the duration, source-range, clip-count and transcript checks.</p>"
                    if selected else "")
        cards.append(f"<article><h2>{safe(decision['candidate_id'])} · {safe(decision['verdict'])}</h2>"
                     f"{interval}{preview}<p>{safe(decision['reason'])}</p>{notes}{evidence}{warnings}</article>")
    campaign = report["campaign"]
    coverage = report["coverage"]
    provider = report["provider"]
    if provider["provider"] == "heuristic":
        coverage_text = f"Baseline analysis used {coverage['available_utterances']} available utterances as timing input."
    else:
        coverage_text = (f"{coverage['scanned_utterances']} of {coverage['available_utterances']} available utterances "
                         "scanned by the Director.")
    caption_text = "Captions required" if campaign["captions"] else "Captions optional"
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        "<title>Chomp-Chomp Director review</title><style>body{font:1rem/1.55 system-ui;margin:3rem auto;"
        "max-width:58rem;padding:0 1.25rem;background:#f6f7f8;color:#18232b}article{padding:1rem 1.5rem;"
        "border:1px solid #cad3d9;border-radius:.5rem;background:white;margin:1rem 0}video{width:100%;"
        "max-height:24rem;background:#142027}h1,h2{line-height:1.2}</style>"
        f"<h1>Chomp-Chomp Director review</h1><p>{safe(media.name)} · {safe(provider['provider'])}"
        f" {safe(provider.get('model'))} · {report['selected_count']} of {campaign['clips']} possible clips selected</p>"
        f"<p>{safe(campaign['platform'])} · {safe(campaign['aspect_ratio'])} · "
        f"{campaign['min_duration']}–{campaign['max_duration']} s · Target {campaign['duration']} s · {caption_text}</p>"
        f"<p>{coverage_text} Transcript gaps may contain silence, nonverbal activity, or missing speech.</p>"
        "<p>Dry run · Human review pending</p>"
        + ("".join(cards) or "<p>No decisions to review.</p>") + "</html>\n"
    )


def run_clip(media: Path, output: Path, campaign: Campaign, *, provider: SelectionProvider | None = None,
             section: tuple[int, int] | None = None, transcript_path: Path | None = None,
             chat_path: Path | None = None, asr_model: str = "small", audio_stream: int = 0,
             max_calls: int = 120, include_rejected: bool = False, budget_policy: BudgetPolicy | None = None) -> dict:
    started = perf_counter()
    media, output = media.resolve(), output.resolve()
    meta = probe(media)
    duration = meta["duration_ms"]
    check_audio_track(meta, audio_stream)
    section = section or (0, duration)
    start, end = section
    checked_span(start, end, duration)
    source_id = source_identity(media)
    cache = output / ".analysis"
    recorded = asdict(campaign)
    model_campaign = {key: value for key, value in recorded.items() if key != "rights_ref"}
    # Validate direct Python callers with the same campaign rules used by the CLI.
    campaign = resolve_campaign(**asdict(campaign))
    calls = EditorialCalls(provider, cache, source_id, max_calls=max_calls, policy=budget_policy) if provider is not None else None
    fingerprint = calls.fingerprint if calls is not None else {"provider": "heuristic", "model": None}
    transcriber = LocalTranscriber(asr_model) if transcript_path is None and (provider or campaign.captions) else None
    config = {"schema_version": JOB_CONTRACT, "source": str(media), "source_id": source_id, "section_ms": section,
              "campaign_revision": artifact_key(recorded), "campaign": recorded, "provider": fingerprint,
              "transcript_input_sha256": digest_file(transcript_path) if transcript_path else None,
              "chat_input_sha256": digest_file(chat_path) if chat_path else None, "audio_stream": audio_stream,
              "asr_model_requested": asr_model if transcriber else None,
              "asr_fingerprint": transcriber.fingerprint if transcriber else None,
              "max_calls": max_calls if calls is not None else None, "include_rejected": include_rejected}
    if calls:
        config["budget_policy"] = asdict(calls.policy)
    job_id = artifact_key(config)[:24]
    job = output / job_id
    report = {**config, "job_id": job_id, "duration_ms": duration, "mode": "dry_run", "review_status": "pending",
              "status": "running", "candidates": [], "decisions": [], "investigation": [], "selected_count": 0,
              "coverage": {"available_utterances": 0, "scanned_utterances": 0, "windows": [],
                           "no_transcript_ranges_ms": [list(section)]},
              "availability": {"transcript": False, "visual": False, "audio_observations": False,
                               "chat_observations": False},
              "review_page": None}
    proposals, coverage = report["candidates"], report["coverage"]
    with artifact_lock(output / ".locks" / f"{job_id}.lock", timeout=5):
        write_json(job / "director.json", report)
        try:
            transcript = read_transcript(transcript_path, duration)
            if transcriber:
                transcript, _ = transcriber.cached(cache, media, source_id, duration, audio_stream,
                                                   start_ms=start, end_ms=end, words=True)
            index = TranscriptIndex(transcript)
            snapshot = job / "transcript.json"
            write_json(snapshot, {"segments": [asdict(row) for row in transcript]})
            report.update(transcript_snapshot=str(snapshot), transcript_snapshot_sha256=digest_file(snapshot))
            active = index.ids_in(start, end)
            coverage.update(available_utterances=len(active), no_transcript_ranges_ms=index.gaps(start, end))
            report["availability"]["transcript"] = bool(active)
            audio, cached = _audio_curve(cache, media, source_id, audio_stream, end - start, start_ms=start)
            report["audio_cache_hit"] = cached
            observations = [replace(obs, start_ms=obs.start_ms + start, end_ms=obs.end_ms + start)
                            for obs in audio_observations(audio, end - start, hop_ms=_AUDIO_HOP_MS)]
            chat = [msg for msg in read_chat(chat_path, duration) if start <= msg.time_ms < end]
            observations += chat_observations(chat, duration)
            report["availability"].update(audio_observations=True, chat_observations=chat_path is not None)
            baseline = calls is None
            if baseline:
                observations += transcript_observations(transcript, duration)
            else:
                try:
                    scan_dialogue(calls, index, section, model_campaign, coverage["windows"], proposals)
                except DirectorFailure as exc:
                    if exc.status != "discovery_budget_exhausted":
                        raise
                    report["discovery_warning"] = str(exc)
            signals = signal_candidates(observations, section)
            merge_signals(proposals, signals, index)
            decisions = (baseline_decisions(proposals, signals, index, transcript, observations, audio, section,
                                            duration, campaign) if baseline else
                         investigate(calls, index, section, campaign, model_campaign, proposals, report["investigation"]))
            report["decisions"] = enforce(proposals, decisions, index, section, campaign)
            report["selected_count"] = sum(row["verdict"] == "select" for row in report["decisions"])
            report["status"] = "partial_review_pending" if report.get("discovery_warning") else "review_pending"
        except DirectorFailure as exc:
            report.update(status=exc.status, error=str(exc))
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            report.update(status="analysis_error", error=str(exc))
        except KeyboardInterrupt:
            report.update(status="interrupted", error="Interrupted. Validated Director calls can be reused.")
            raise
        finally:
            coverage["scanned_utterances"] = len({uid for window in coverage["windows"]
                                                  if window["status"] == "scanned" for uid in window["utterance_ids"]})
            planned = []
            for left in range(start, end, STRIDE_MS):
                right = min(end, left + WINDOW_MS)
                planned.append((left, right))
                if right == end:
                    break
            completed = [window["range_ms"] for window in coverage["windows"]
                         if window["status"] in {"scanned", "no_transcript"}]
            cursor, uncovered = start, []
            for left, right in sorted(completed):
                if left > cursor:
                    uncovered.append([cursor, left])
                cursor = max(cursor, right)
            if cursor < end:
                uncovered.append([cursor, end])
            coverage["expected_windows"] = len(planned) if calls else None
            coverage["completed_windows"] = len(completed) if calls else None
            coverage["unscanned_ranges_ms"] = uncovered if calls else None
            coverage["discovery_complete"] = bool(calls and len(completed) == len(planned))
            coverage["scope"] = "dialogue_only" if calls else "heuristic_signals"
            coverage["section_ms"] = list(section)
            report["usage"] = {"provider_calls": calls.sent if calls else 0, "calls": calls.records if calls else [],
                               "cost_usd": None if calls else 0,
                               "phase_calls": calls.phase_sent if calls else {},
                               "cache_hits": sum(row["cached"] for row in calls.records) if calls else 0}
            report["elapsed_seconds"] = round(perf_counter() - started, 3)
            if report["status"] in {"review_pending", "partial_review_pending"}:
                page = job / "review.html"
                write_text(page, review_html(report, media, include_rejected=include_rejected))
                report["review_page"] = str(page)
            write_json(job / "director.json", report)
    return {**report, "job_report": str(job / "director.json")}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="chomp clip", description="Select source-clock highlights for human review")
    parser.add_argument("source", type=Path)
    parser.add_argument("--director", required=True, choices=("codex", "heuristic"))
    parser.add_argument("--model", help="explicit Codex model ID")
    parser.add_argument("--campaign", type=Path, help="reusable campaign JSON")
    parser.add_argument("--brief")
    parser.add_argument("--clips", type=int)
    parser.add_argument("--duration", type=float, help="target seconds")
    parser.add_argument("--min-duration", type=float)
    parser.add_argument("--max-duration", type=float)
    parser.add_argument("--rights-ref")
    parser.add_argument("--platform")
    parser.add_argument("--aspect-ratio")
    parser.add_argument("--preferred-topic", dest="preferred_topics", action="append")
    parser.add_argument("--excluded-topic", dest="excluded_topics", action="append")
    parser.add_argument("--captions", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--section", nargs=2, metavar=("START", "END"), help="source start and end in seconds")
    parser.add_argument("--transcript", type=Path, help="use timestamped transcript JSON instead of local ASR")
    parser.add_argument("--asr-model", default="small", help="cached Whisper model name or local model directory")
    parser.add_argument("--chat", type=Path, help="optional local livestream chat replay for signal discovery")
    parser.add_argument("--audio-stream", type=int, default=0)
    parser.add_argument("--max-calls", type=int, default=120, help="maximum new Codex calls per run")
    parser.add_argument("--discovery-calls", type=int, help="new scan calls; defaults to total minus final reserve")
    parser.add_argument("--investigation-calls", type=int, default=4)
    parser.add_argument("--final-reserve", type=int, default=1)
    parser.add_argument("--provider-timeout", type=float, default=150)
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="generate an editorial review without export (default)")
    parser.add_argument("--render", action="store_true", help="opt in to validated review exports")
    parser.add_argument("--acknowledge-permission", action="store_true")
    args = parser.parse_args(argv)
    if (args.director == "codex") != bool(args.model):
        parser.error("--director codex requires --model; heuristic has no model")
    if not 1 <= args.max_calls <= 300:
        parser.error("--max-calls must be between 1 and 300")
    if args.render and (args.dry_run or not args.acknowledge_permission):
        parser.error("--render requires --acknowledge-permission and cannot accompany --dry-run")
    try:
        campaign = resolve_campaign(args.campaign, **{key: getattr(args, key) for key in (
            "brief", "clips", "duration", "min_duration", "max_duration", "rights_ref", "platform", "aspect_ratio",
            "preferred_topics", "excluded_topics", "captions")})
        section = tuple(map(milliseconds, args.section)) if args.section else None
        provider = CodexCLI(args.model, effort="low") if args.model else None
        reserve = min(args.final_reserve, args.max_calls - 1)
        policy = BudgetPolicy(args.discovery_calls if args.discovery_calls is not None else args.max_calls - reserve,
                              args.investigation_calls, reserve, args.provider_timeout)
        report = run_clip(args.source, args.output, campaign, provider=provider, section=section,
                          transcript_path=args.transcript, chat_path=args.chat, asr_model=args.asr_model,
                          audio_stream=args.audio_stream, max_calls=args.max_calls, include_rejected=args.include_rejected,
                          budget_policy=policy)
        if args.render and report["status"] == "review_pending":
            from .production import export_selection
            report["production"] = export_selection(Path(report["job_report"]), args.output,
                                                      acknowledge_permission=True)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "input_error", "error": str(exc)}), file=sys.stderr)
        return 1
    summary = {key: report[key] for key in ("status", "job_report", "review_page", "selected_count")}
    summary["coverage"] = {key: report["coverage"][key] for key in ("available_utterances", "scanned_utterances")}
    summary["selected"] = [{"candidate_id": row["candidate_id"], **row["boundary"], "reason": row["reason"]}
                           for row in report["decisions"] if row["verdict"] == "select"]
    if "error" in report:
        summary["error"] = report["error"]
    if "production" in report:
        summary["review_manifest"] = report["production"]["review_manifest"]
    print(json.dumps(summary))
    return int(report["status"] != "review_pending")
