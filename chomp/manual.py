"""Exact-timestamp preview export independent of provisional moment selection."""
from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path

from .captions import make_srt
from .media import probe, render_preview, exported_audio_level, resolve_layout
from .models import checked_span
from .signals import read_transcript


def render_manual_preview(
    source: Path, output_root: Path, *, rights_ref: str, start_s: float, end_s: float,
    transcript_path: Path | None = None,
    facecam_roi: tuple[int, int, int, int] | None = None,
    gameplay_roi: tuple[int, int, int, int] | None = None,
    layout: str = "auto", audio_stream: int = 0,
) -> dict:
    if not rights_ref.strip():
        raise ValueError("A non-empty rights reference is required")
    if not all(math.isfinite(x) for x in (start_s, end_s)):
        raise ValueError("Manual timestamps must be finite numbers")
    start_ms, end_ms = round(start_s * 1000), round(end_s * 1000)
    source = source.expanduser().resolve()
    meta = probe(source)
    checked_span(start_ms, end_ms, meta["duration_ms"])
    chosen_layout = resolve_layout(layout, facecam_roi)
    config = json.dumps({"path":str(source), "size":source.stat().st_size,
                         "mtime_ns":source.stat().st_mtime_ns,"start":start_ms,"end":end_ms,
                         "layout":chosen_layout,"facecam":facecam_roi,"gameplay":gameplay_roi,
                         "audio_stream":audio_stream,"rights_ref":rights_ref,
                         "transcript_hash":sha256(transcript_path.read_bytes()).hexdigest() if transcript_path else None},
                        sort_keys=True,ensure_ascii=False)
    job_id = "manual-" + sha256(config.encode()).hexdigest()[:16]
    out = output_root.expanduser().resolve() / job_id
    out.mkdir(parents=True, exist_ok=True)
    srt_file = None
    if transcript_path is not None:
        text, count = make_srt(read_transcript(transcript_path, meta["duration_ms"]),start_ms,end_ms,meta["duration_ms"])
        if text:
            srt_file = out / "manual_preview.srt"
            srt_file.write_text(text,encoding="utf-8")
    mp4 = out / "manual_preview.mp4"
    render_preview(source, mp4, start_ms, end_ms, captions_path=srt_file,
                   facecam_roi=facecam_roi, gameplay_roi=gameplay_roi,
                   layout=chosen_layout, audio_stream=audio_stream)
    level = exported_audio_level(mp4)
    report = {"job_id":job_id,"source":str(source),"preview":str(mp4),
              "start_ms":start_ms,"end_ms":end_ms,"duration_ms":end_ms-start_ms,
              "layout":chosen_layout,"facecam_roi_px":facecam_roi,
              "gameplay_roi_px":gameplay_roi,"audio_stream":audio_stream,
              "captions_srt":str(srt_file) if srt_file else None,
              "audio_mean_dbfs":level,
              "audio_warning":level < -60,
              "rights_ref":rights_ref,"review_status":"pending","publish_status":"disabled"}
    (out / "manual_review.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    return report
