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
from .boundaries import refine_boundaries
from .captions import make_srt
from .media import exported_audio_level, probe, render_preview, scan_audio, resolve_layout
from .models import TranscriptSegment
from .ranking import heuristic_rank, select
from .speakers import read_speaker_turns, attribute_segments, candidate_speaker_context, speaker_inventory
from .signals import audio_observations, chat_observations, read_chat, read_transcript, transcript_observations


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as temp:
        json.dump(value, temp, indent=2, ensure_ascii=False)
        tmp = Path(temp.name)
    tmp.replace(path)


def _job_id(path: Path, settings: dict) -> str:
    """Key all output-affecting inputs so reruns cannot silently reuse stale edits."""
    stats = path.stat()
    payload = {"media_path":str(path.resolve()),"media_size":stats.st_size,"media_mtime_ns":stats.st_mtime_ns,"settings":settings}
    return sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def transcribe_media(media: Path, model: str = "small", device: str = "cpu") -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("ASR requires: pip install '.[asr]' (or supply --transcript)") from exc
    engine = WhisperModel(model, device=device, compute_type="int8" if device=="cpu" else "float16")
    output, _ = engine.transcribe(str(media), vad_filter=True, beam_size=3)
    return [{"start_ms":round(s.start*1000), "end_ms":round(s.end*1000), "text":s.text} for s in output]


def process(media: Path, output_root: Path, *, rights_ref: str, transcript_path: Path | None = None, chat_path: Path | None = None, chat_offset_ms: int = 0, speaker_turns_path: Path | None = None, main_speaker_id: str | None = None, transcribe: bool = False, asr_model: str = "small", asr_device: str = "cpu", min_score: float = 45, limit: int = 3, render: bool = False, facecam_roi: tuple[int,int,int,int] | None = None, gameplay_roi: tuple[int,int,int,int] | None = None, layout: str = "auto", audio_stream: int = 0) -> dict:
    if not rights_ref.strip():
        raise ValueError("A nonempty rights reference is required")
    if not 0 <= limit <= 10 or not 0 <= min_score <= 100:
        raise ValueError("limit must be 0..10 and score threshold 0..100")
    media = media.expanduser().resolve()
    meta = probe(media)
    duration_ms = meta["duration_ms"]
    def _aux_identity(path: Path | None) -> str | None:
        return sha256(path.read_bytes()).hexdigest() if path is not None else None
    config = {
        "rights_ref": rights_ref,
        "transcript_hash": _aux_identity(transcript_path),
        "chat_hash": _aux_identity(chat_path),
        "speaker_turns_hash": _aux_identity(speaker_turns_path),
        "main_speaker_id": main_speaker_id,
        "chat_offset_ms": chat_offset_ms,
        "transcribe": transcribe,
        "asr_model": asr_model if transcribe else None,
        "asr_device": asr_device if transcribe else None,
        "min_score": min_score,
        "limit": limit,
        "render": render,
        "facecam_roi": facecam_roi,
        "gameplay_roi": gameplay_roi,
        "layout": layout,
        "audio_stream": audio_stream,
        # Cutline and CaptionCraft may change timings and assets for the same inputs.
        "pipeline_version": "0.4.0+integ09",
    }
    job_id = _job_id(media,config)
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
        turns = read_speaker_turns(speaker_turns_path, duration_ms)
        transcript = attribute_segments(transcript, turns)
        speaker_ids = speaker_inventory(transcript, turns, main_speaker_id)
        chat = read_chat(chat_path, duration_ms, chat_offset_ms)
        audio = scan_audio(media, audio_stream=audio_stream)
        observations = audio_observations(audio,duration_ms) + transcript_observations(transcript,duration_ms) + chat_observations(chat,duration_ms)
        candidates = discover(observations, transcript, duration_ms)
        boundary_decisions = {}
        for c in candidates:
            boundary_decisions[id(c)] = asdict(refine_boundaries(c, transcript, duration_ms, audio_levels=audio))
            c.speaker_context = candidate_speaker_context(transcript, c.start_ms, c.end_ms, main_speaker_id)
            heuristic_rank(c)
        ranking_warnings = []
        selected = select(candidates, max_clips=limit, min_score=min_score)
        assets = []
        for i,c in enumerate(selected,1):
            file_name = f"candidate_{i:02d}.mp4"
            subtitles, cue_count = make_srt(transcript,c.start_ms,c.end_ms,duration_ms)
            srt_name = f"candidate_{i:02d}.srt"
            srt_path = job_dir/srt_name
            if subtitles:
                srt_path.write_text(subtitles, encoding="utf-8")
            elif srt_path.exists():
                srt_path.unlink()
            audio_mean_dbfs = None
            if render:
                render_preview(media,job_dir/file_name,c.start_ms,c.end_ms,captions_path=srt_path if subtitles else None,facecam_roi=facecam_roi,gameplay_roi=gameplay_roi,layout=layout,audio_stream=audio_stream)
                audio_mean_dbfs = exported_audio_level(job_dir/file_name)
                if audio_mean_dbfs < -60:
                    ranking_warnings.append(f"candidate_{i:02d} audio is very quiet/silent ({audio_mean_dbfs} dBFS); inspect original recording and playback")
            edit_spec = {"schema_version":"0.4", "source_start_ms":c.start_ms,"source_end_ms":c.end_ms,"layout":resolve_layout(layout,facecam_roi),"facecam_roi_px":list(facecam_roi) if facecam_roi else None,"gameplay_roi_px":list(gameplay_roi) if gameplay_roi else None,"audio_stream":audio_stream,"caption_source":"provided_or_ASR_segments" if cue_count else "unavailable", "caption_timing":"segment_estimated_not_word_aligned" if cue_count else "unavailable","width":540,"height":960,"human_review_required":True}
            _write_json(job_dir/f"candidate_{i:02d}.edit.json",edit_spec)
            assets.append({"candidate":c.to_dict(),"boundary":boundary_decisions[id(c)],"edit_spec":str(job_dir/f"candidate_{i:02d}.edit.json"),"captions_srt":str(srt_path) if subtitles else None,"caption_cues":cue_count,"preview":str(job_dir/file_name) if render else None, "audio_mean_dbfs":audio_mean_dbfs, "review_status":"pending", "publish_status":"disabled", "rights_ref":rights_ref})
        manifest = {"schema_version":"0.4", "job_id":job_id,"source":str(media),"duration_ms":duration_ms,"rights_ref":rights_ref,"chat_status":"available" if chat else "missing","transcript_status":"available" if transcript else "missing", "speaker_attribution_status":"turns_imported" if turns else "transcript_labelled" if speaker_ids else "unavailable", "speaker_ids":speaker_ids, "main_speaker_id":main_speaker_id, "speaker_identification_notice":"Speaker labels are anonymous unless main_speaker_id has been confirmed externally; plain Whisper ASR cannot identify the streamer.", "ranking_notice":"Heuristic discovery/ranking only; NO AI model or agent integration is active. Not a virality predictor.", "candidate_count":len(candidates),"selected_for_review":len(assets),"warnings":ranking_warnings,"clips":assets,"manual_review_required":True, "auto_publishing":False}
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
