"""Single-file VOD pipeline with explicit review gate and resumable ASR cache."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import tempfile
from datetime import datetime, timezone

from .discovery import discover
from .media import probe, render_preview, scan_audio
from .models import TranscriptSegment
from .ranking import heuristic_rank, ollama_rank, select
from .signals import audio_observations, chat_observations, read_chat, read_transcript, transcript_observations


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as temp:
        json.dump(value, temp, indent=2, ensure_ascii=False)
        tmp = Path(temp.name)
    tmp.replace(path)


def _job_id(path: Path) -> str:
    stats = path.stat()
    return sha256(f"{path.resolve()}:{stats.st_size}:{stats.st_mtime_ns}".encode()).hexdigest()[:16]


def transcribe_media(media: Path, model: str = "small", device: str = "cpu") -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("ASR requires: pip install '.[asr]' (or supply --transcript)") from exc
    engine = WhisperModel(model, device=device, compute_type="int8" if device=="cpu" else "float16")
    output, _ = engine.transcribe(str(media), vad_filter=True, beam_size=3)
    return [{"start_ms":round(s.start*1000), "end_ms":round(s.end*1000), "text":s.text} for s in output]


def process(media: Path, output_root: Path, *, rights_ref: str, transcript_path: Path | None = None, chat_path: Path | None = None, chat_offset_ms: int = 0, transcribe: bool = False, asr_model: str = "small", asr_device: str = "cpu", ollama_model: str | None = None, min_score: float = 45, limit: int = 3, render: bool = False) -> dict:
    if not rights_ref.strip():
        raise ValueError("A nonempty rights reference is required")
    if not 0 <= limit <= 10 or not 0 <= min_score <= 100:
        raise ValueError("limit must be 0..10 and score threshold 0..100")
    media = media.expanduser().resolve()
    meta = probe(media)
    duration_ms = meta["duration_ms"]
    job_id = _job_id(media)
    job_dir = output_root.resolve() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(output_root.resolve()/"jobs.sqlite")
    try:
        database.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, source TEXT, status TEXT, modified_utc TEXT, manifest TEXT)")
        database.execute("INSERT INTO jobs(job_id,source,status,modified_utc,manifest) VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, modified_utc=excluded.modified_utc", (job_id,str(media),"running",datetime.now(timezone.utc).isoformat(),str(job_dir/"review_queue.json")))
        database.commit()
        if transcript_path:
            transcript = read_transcript(transcript_path, duration_ms)
        elif transcribe:
            cache = job_dir / f"asr-{asr_model}-{asr_device}.json"
            if not cache.exists():
                _write_json(cache, {"segments":transcribe_media(media,asr_model,asr_device)})
            transcript = read_transcript(cache,duration_ms)
        else:
            transcript = []
        chat = read_chat(chat_path, duration_ms, chat_offset_ms)
        audio = scan_audio(media)
        observations = audio_observations(audio,duration_ms) + transcript_observations(transcript,duration_ms) + chat_observations(chat,duration_ms)
        candidates = discover(observations, transcript, duration_ms)
        ranking_warnings = []
        for c in candidates:
            heuristic_rank(c)
            if ollama_model:
                try:
                    ollama_rank(c, ollama_model)
                except (ValueError, KeyError, RuntimeError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                    ranking_warnings.append(f"LLM unavailable for candidate at {c.anchor_ms}: {str(exc)[:140]}")
        selected = select(candidates, max_clips=limit, min_score=min_score)
        assets = []
        for i,c in enumerate(selected,1):
            file_name = f"candidate_{i:02d}.mp4"
            if render:
                render_preview(media,job_dir/file_name,c.start_ms,c.end_ms)
            assets.append({"candidate":c.to_dict(), "preview":str(job_dir/file_name) if render else None, "review_status":"pending", "publish_status":"disabled", "rights_ref":rights_ref})
        manifest = {"schema_version":"0.1", "job_id":job_id,"source":str(media),"duration_ms":duration_ms,"rights_ref":rights_ref,"chat_status":"available" if chat else "missing","transcript_status":"available" if transcript else "missing", "ranking_notice":"Uncalibrated quality estimates; never validated virality predictions", "candidate_count":len(candidates),"selected_for_review":len(assets),"warnings":ranking_warnings,"clips":assets,"manual_review_required":True, "auto_publishing":False}
        _write_json(job_dir/"review_queue.json",manifest)
        database.execute("UPDATE jobs SET status=?,modified_utc=? WHERE job_id=?",("review_pending",datetime.now(timezone.utc).isoformat(),job_id))
        database.commit()
        return manifest
    except Exception:
        database.execute("UPDATE jobs SET status=?,modified_utc=? WHERE job_id=?",("failed",datetime.now(timezone.utc).isoformat(),job_id))
        database.commit()
        raise
    finally:
        database.close()
