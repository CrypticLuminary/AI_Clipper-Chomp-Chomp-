"""Private, one-job subprocess entry point for Studio and the job CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from ..artifacts import artifact_lock, write_json
from ..clipcontrol import ClipControl
from ..director.provider import CodexCLI
from ..evolution import EvolutionConfig
from ..manual import rerender
from ..pipeline import process
from ..streambridge.acquire import acquire, verify_local
from ..streambridge.live import LiveOptions, monitor_live, run_live
from ..streambridge.source import BridgeError
from .jobs import Store, read, safe_path


def recording(store, jid, cfg, secrets, requirements, tick):
    tick("resolving_source")
    path = (store.asset(cfg["upload"]) if cfg.get("upload") else
            Path(cfg["local_file"]).expanduser().resolve() if cfg.get("local_file") else None)
    transcript = (store.asset(cfg["transcript_upload"], transcript=True) if cfg.get("transcript_upload") else
                  Path(cfg["local_transcript"]).expanduser().resolve() if cfg.get("local_transcript") else None)
    if cfg.get("upload"):
        verify_local(path, remote=True)
    source = cfg.get("source") or secrets.get("source")
    remote = secrets.get("media_url")
    if path and remote:
        raise ValueError("Choose either the original file or the creator media URL")
    link = None
    if source or remote:
        if not source and remote:
            source = remote
        tick("acquiring_media")
        acquired = acquire(source, store.root / "acquisition", rights_ref=cfg["rights_ref"], confirmed=True,
                           media_file=path, media_url=remote if not path else None,
                           twitch_download=cfg.get("twitch_download", False),
                           progress=lambda s: tick("acquiring_media", bytes_received=s.get("bytes_received"),
                                                    bytes_total=s.get("bytes_total")))
        path, link = acquired.path, acquired.provenance()
    if not path:
        raise BridgeError("media_unavailable", "Attach an authorized recording or creator media URL.")
    evolution, provider = models(cfg, requirements)
    tick("processing_source")
    result = process(path, store.root / "runs", rights_ref=cfg["rights_ref"], transcript_path=transcript,
                     transcribe=cfg.get("transcribe", True), asr_model=cfg.get("asr_model") or "small",
                     asr_device=cfg.get("asr_device", "cpu"), render=True, requirements=requirements,
                     evolution=evolution, evolution_provider=provider, progress=tick, source_link=link)
    return result, store.root / "runs" / result["job_id"] / "review_queue.json"


def models(cfg, requirements):
    def optional(name):
        value = os.getenv(name)
        return Path(value) if value else None
    provider = CodexCLI(cfg["provider_model"], effort="low") if cfg.get("provider_model") else None
    evolution = EvolutionConfig(profile=requirements.hardware, embeddings=optional("CHOMP_STUDIO_MINILM"),
        audio_model=optional("CHOMP_STUDIO_YAMNET"), visual_model=optional("CHOMP_STUDIO_CLIP"),
        min_ms=requirements.bounds[0], max_ms=requirements.bounds[1],
        reasoning_calls=requirements.calls, reasoning_seconds=requirements.reasoning_seconds)
    return evolution, provider


def live(store, jid, cfg, secrets, requirements, tick):
    tick("resolving_source")
    spool = Path(cfg["local_spool"]).expanduser().resolve() if cfg.get("local_spool") else None
    feed = secrets.get("feed_url")
    value = cfg.get("source") or secrets.get("source") or feed or (str(spool) if spool else None)
    if not value:
        raise BridgeError("media_unavailable", "Attach an authorized HLS feed or a CLI producer spool to capture media.")
    evo, provider = models(cfg, requirements)
    live_id = jid
    result = run_live(value, store.root / "live", rights_ref=cfg["rights_ref"], confirmed=True,
        spool=spool, feed_url=feed, job_id=live_id,
        options=LiveOptions(max_seconds=cfg.get("live_seconds", 300), asr_model=(cfg.get("asr_model") or "small")
                            if cfg.get("transcribe", True) else None, asr_device=cfg.get("asr_device", "cpu"),
                            idle_seconds=30, max_clips=requirements.clips, min_ms=requirements.bounds[0],
                            max_ms=requirements.bounds[1], audio_stream=requirements.audio_stream,
                            layout=requirements.layout, hardware_profile=requirements.hardware, render=True,
                            requirements=requirements, evolution=evo, provider=provider),
        progress=lambda s: tick("capturing", ingested_chunks=s.get("ingested_chunks", 0),
                               clock_ms=s.get("clock_ms", 0), pending=len(s.get("pending", [])),
                               produced=len(s.get("ready", [])), feed_status=s["status"]))
    clips = []
    for item in result.get("ready", ()):
        if item.get("preview"):
            preview = safe_path(store.root, item["preview"])
            report = read(preview.with_name("manual_review.json"))
            clips.append({**report, "start_ms": item["start_ms"], "end_ms": item["end_ms"],
                          "window_start_ms": item["window_start_ms"], "candidate_id": item["candidate_id"],
                          "candidate": {"start_ms": item["start_ms"], "end_ms": item["end_ms"],
                            "ranking_method": item.get("ranking_method"), "rationale": item.get("rationale"),
                            "transcript_excerpt": item.get("description")}})
    manifest = {"job_id": jid, "clips": clips, "candidate_count": len(result.get("ready", ())) + len(result.get("pending", ())),
                "duration_ms": result.get("clock_ms"), "requirements": requirements.public(),
                "count_result": requirements.export_summary(len(clips)), "analysis_status": "partial"
                if result["status"] != "ended" else "complete", "warnings": [result.get("stop_reason", "Capture finished")]}
    path = store.folder(jid) / "live-review.json"
    write_json(path, manifest)
    return manifest, path


def main(root, jid):
    store = Store(root)
    cfg = read(store.folder(jid) / "request.json")
    secrets = json.load(sys.stdin)

    def tick(stage, **details):
        if (store.folder(jid) / "stop").exists():
            raise InterruptedError("Cancelled")
        store.update(jid, stage=stage, progress={k: v for k, v in details.items() if v is not None})

    try:
        with artifact_lock(store.root / ".engine.lock", timeout=7200):
            if cfg["kind"] == "revision":
                tick("rendering")
                report = rerender(safe_path(store.root, cfg["edit_path"]), store.root / "revisions",
                    start_s=cfg["start_ms"] / 1000, end_s=cfg["end_ms"] / 1000,
                    facecam_roi=cfg.get("facecam_roi"), gameplay_roi=cfg.get("gameplay_roi"), layout_plan=None,
                    layout=cfg.get("layout"), protected_hud_roi=cfg.get("protected_hud_roi"))
                store.update(jid, status="completed", stage="completed", revision_report=report["review_manifest"])
                return 0
            if cfg["kind"] == "monitor":
                tick("resolving_source")
                result = monitor_live(cfg["source"], store.root / "live", rights_ref=cfg["rights_ref"], confirmed=True,
                    seconds=cfg.get("live_seconds", 300), interval=15, job_id=jid,
                    progress=lambda state: tick("monitoring", live_status=state.get("live_status"),
                                               media_status=state.get("media_status")))
                store.update(jid, status="completed", stage="completed", produced=0,
                             progress={"live_status": result.get("live_status"), "media_status": "unavailable"},
                             monitor_summary="Metadata only. No media was captured.")
                return 0
            control = ClipControl.parse(cfg["requirements"])
            operation = live if cfg["kind"] == "live" else recording
            result, path = operation(store, jid, cfg, secrets, control, tick)
            status = "partial" if result.get("analysis_status") == "partial" or result.get("count_result", {}).get("shortfall") else "completed"
            store.update(jid, status=status, stage=status, manifest=str(path),
                         count_result=result.get("count_result"), candidate_count=result.get("candidate_count", 0),
                         produced=len(result.get("clips", [])), warnings=result.get("warnings", [])[:60],
                         progress={"produced": len(result.get("clips", [])), "candidates": result.get("candidate_count", 0)},
                         timing=result.get("timing"))
            return 0
    except InterruptedError:
        store.update(jid, status="cancelled", stage="cancelled")
    except (BridgeError, FileNotFoundError, ValueError, KeyError, OSError, TypeError, RuntimeError) as exc:
        if isinstance(exc, BridgeError):
            error = exc.public()
        elif isinstance(exc, (ValueError, RuntimeError)):
            error = {"code": "processing_failed", "message": str(exc)[:500]}
        else:
            error = {"code": "processing_failed", "message": "The local worker could not read the source or finish processing."}
        store.update(jid, status="failed", stage="failed", error=error)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), sys.argv[2]))
