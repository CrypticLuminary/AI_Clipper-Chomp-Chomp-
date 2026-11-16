"""Clip plans, preview artifacts and human approval of exact revisions."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import re
from urllib.parse import quote

from .artifacts import digest_file, write_json, write_text
from .captions import CaptionResult, build_captions
from .editorial import quote_title, validate_title
from .framing import FramingPlan
from .media import LayoutSegment, crop_position, exported_audio_level, exported_loudness, render_preview
from .models import TranscriptSegment, checked_span
from .sound import SoundCue, cues_in_range

SCHEMA = "0.5"
PROFILES = {"preview": (540, 960), "final": (1080, 1920)}
_SRT_TIME = re.compile(r"(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})")


def layout_segments(data: object, start_ms: int, end_ms: int) -> tuple[LayoutSegment, ...]:
    if not isinstance(data, list) or len(data) > 24 or not data:
        raise ValueError("A layout plan needs one to 24 ordered segments")
    result = []
    previous_end = None
    for row in data:
        if not isinstance(row, dict):
            raise ValueError("Layout segments must be objects")
        seg = LayoutSegment(**row)
        if (type(seg.start_ms) is not int or type(seg.end_ms) is not int or not 0 <= seg.start_ms < seg.end_ms
                or type(seg.fit_facecam) is not bool or type(seg.preserve_gameplay) is not bool):
            raise ValueError("Invalid layout interval or flags")
        if previous_end is not None and previous_end != seg.start_ms:
            raise ValueError("Layout intervals must be adjacent and in source order")
        previous_end = seg.end_ms
        left, right = max(start_ms, seg.start_ms), min(end_ms, seg.end_ms)
        if left < right:
            path = seg.gameplay_path
            if path and (left, right) != (seg.start_ms, seg.end_ms):
                if any(not isinstance(k, (list, tuple)) or len(k) != 3 or any(type(v) is not int for v in k)
                       for k in path) or any(a[0] >= b[0] for a, b in zip(path, path[1:])):
                    raise ValueError("Invalid gameplay keyframes in layout")
                path = ((left, *crop_position(path, left)),
                        *(key for key in path if left < key[0] < right),
                        (right, *crop_position(path, right)))
            result.append(replace(seg, start_ms=left, end_ms=right, gameplay_path=path))
    if not result or result[0].start_ms != start_ms or result[-1].end_ms != end_ms:
        raise ValueError("The supplied layout plan must cover the entire clip")
    return tuple(result)


def read_layout_plan(path: Path | None) -> list[dict] | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or "segments" not in data:
        raise ValueError("Layout plan requires a segments array")
    return data["segments"]


def _caption_override(path: Path, duration_ms: int, *, shorts_style: bool = False) -> CaptionResult:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return CaptionResult("", 0, "unavailable", ())
    entries = re.split(r"\n[ \t]*\n", text)
    previous_end = 0
    for entry in entries:
        lines = entry.splitlines()
        if len(lines) < 3 or not lines[0].isdigit():
            raise ValueError("Corrected captions must be a valid clip-local SRT")
        if shorts_style and len(lines) > 4:
            raise ValueError("Shorts captions support at most two lines per cue")
        stamps = lines[1].split(" --> ")
        ms = []
        for stamp in stamps:
            match = _SRT_TIME.fullmatch(stamp)
            if not match:
                break
            h, m, s, fraction = map(int, match.groups())
            ms.append(((h * 60 + m) * 60 + s) * 1000 + fraction)
        if len(ms) != 2 or not previous_end <= ms[0] < ms[1] <= duration_ms:
            raise ValueError("Corrected caption cues must be sequential and inside the clip")
        previous_end = ms[1]
    return CaptionResult(text + "\n", len(entries), "editor_supplied", ())


def create_clip(
    work: Path, published: Path, stem: str, *, source: Path, source_id: str, rights_ref: str,
    start_ms: int, end_ms: int, duration_ms: int, transcript: list[TranscriptSegment],
    transcript_path: Path | None, transcript_sha256: str | None, framing: FramingPlan,
    gameplay_roi: tuple | None = None, audio_stream: int = 0, render: bool = False,
    profile: str = "preview", caption_position: str = "lower", audio_profile: str = "original",
    captions_override: Path | None = None, layout_override: list[dict] | None = None,
    shorts_style: bool = False,
    title: str | None = None, title_source: str | None = None, burn_hook: bool = False,
    sound_cues: tuple[SoundCue, ...] = (),
) -> dict:
    checked_span(start_ms, end_ms, duration_ms)
    if profile not in PROFILES:
        raise ValueError("Choose the preview or final export profile")
    width, height = PROFILES[profile]
    warnings = list(framing.warnings)
    if layout_override is not None:
        segments = layout_segments(layout_override, start_ms, end_ms)
        layout = "temporal" if len(segments) > 1 else segments[0].layout
        use_roi = segments[0].facecam_roi if len(segments) == 1 else None
        warnings.append("Review the supplied layout plan and all transitions")
    else:
        layout = framing.layout
        use_roi = framing.facecam_roi
        # Auto fallback uses the actual whole source. A supplied gameplay crop
        # can still be used in an explicitly chosen manual fit.
        actual_game = None if framing.detection and layout == "fit" else gameplay_roi
        segments = framing.segments or (LayoutSegment(start_ms, end_ms, layout, use_roi, actual_game),)
        if framing.segments and gameplay_roi is not None:
            segments = tuple(replace(s, gameplay_roi=gameplay_roi, gameplay_path=(), preserve_gameplay=True)
                             if s.layout != "letterbox" else s for s in segments)
    has_gameplay_crop = any(s.gameplay_roi is not None for s in segments)
    if has_gameplay_crop:
        warnings.append("Confirm the selected gameplay region retains essential action and HUD elements")

    if title is None and shorts_style:
        title = quote_title(transcript, start_ms, end_ms)
        if title:
            title_source = "transcript_quote"
    if title is not None:
        title = validate_title(title)
    captions = (_caption_override(captions_override, end_ms - start_ms, shorts_style=shorts_style) if captions_override else
                build_captions(transcript, start_ms, end_ms, duration_ms))
    warnings.extend(captions.warnings)
    if not captions.count:
        warnings.append("No captions for this interval. Confirm whether speech requires subtitles.")
    if cues_in_range(sound_cues, start_ms, end_ms):
        warnings.append("Check the timing and level of the added sound cues during playback.")
    srt_name, mp4_name, edit_name = f"{stem}.srt", f"{stem}.mp4", f"{stem}.edit.json"
    srt = work / srt_name if captions.count else None
    if srt:
        write_text(srt, captions.text)

    plan = {
        "schema_version": SCHEMA, "source": str(source), "source_id": source_id,
        "source_duration_ms": duration_ms, "source_start_ms": start_ms, "source_end_ms": end_ms,
        "rights_ref": rights_ref, "layout": layout, "facecam_roi_px": use_roi,
        "gameplay_roi_px": segments[0].gameplay_roi if len(segments) == 1 else None,
        "segments": [asdict(segment) for segment in segments],
        "framing_warnings": list(framing.warnings),
        "audio_stream": audio_stream, "audio_profile": audio_profile, "caption_position": caption_position,
        "caption_timing": captions.timing, "transcript_path": str(transcript_path) if transcript_path else None,
        "captions_path": str(published / srt_name) if srt else None,
        "captions_sha256": digest_file(srt) if srt else None,
        "transcript_sha256": transcript_sha256, "width": width, "height": height, "profile": profile,
        "shorts_style": shorts_style,
        "title": title, "title_source": title_source if title else None, "burn_hook": burn_hook,
        "human_review_required": True, "fit_facecam": framing.fit_facecam,
    }
    if framing.detection:
        plan["facecam_detection"] = framing.detection
    if framing.subject_tracking is not None:
        plan["subject_tracking"] = framing.subject_tracking
    if sound_cues:
        plan["sound_cues"] = [asdict(cue) for cue in sound_cues]
    write_json(work / edit_name, plan)

    mean = None
    loudness = None
    preview_hash = None
    audio_warning = False
    if render:
        mp4 = work / mp4_name
        render_preview(source, mp4, start_ms, end_ms, width, height,
                       captions_path=srt, audio_stream=audio_stream, segments=segments,
                       caption_position=caption_position, audio_profile=audio_profile, shorts=shorts_style,
                       hook=title if burn_hook else None, sound_cues=sound_cues)
        measured = exported_audio_level(mp4)
        mean = measured if math.isfinite(measured) else None
        audio_warning = mean is None or mean < -55
        loudness = exported_loudness(mp4)
        if audio_warning:
            warnings.append("Export audio is very quiet or silent. Compare with the source.")
        if loudness["true_peak_dbtp"] is not None and loudness["true_peak_dbtp"] > -.2:
            warnings.append("Export audio true peak is near or above clipping")
        preview_hash = digest_file(mp4)

    return {
        "preview": str(published / mp4_name) if render else None,
        "edit_spec": str(published / edit_name),
        "captions_srt": str(published / srt_name) if srt else None,
        "caption_cues": captions.count, "caption_timing": captions.timing, "audio_mean_dbfs": mean,
        "audio_warning": audio_warning, "loudness": loudness, "review_status": "pending",
        "layout_review_required": bool(framing.warnings or layout_override or has_gameplay_crop),
        "publish_status": "disabled", "rights_ref": rights_ref, "warnings": list(dict.fromkeys(warnings)),
        "artifact_sha256": {"preview": preview_hash, "edit_spec": digest_file(work / edit_name),
                            "captions_srt": plan["captions_sha256"]},
        "layout": layout, "facecam_roi_px": use_roi, "gameplay_roi_px": plan["gameplay_roi_px"],
        "audio_stream": audio_stream, "fit_facecam": framing.fit_facecam,
        "title": title, "title_source": title_source if title else None,
        **({"facecam_detection": framing.detection} if framing.detection else {}),
        **({"subject_id": framing.subject_tracking["subject_id"]} if framing.subject_tracking else {}),
    }


def review_page(path: Path, clips: list[dict], title: str) -> None:
    items = []
    for index, clip in enumerate(clips, 1):
        candidate = clip.get("candidate", clip)
        start = candidate["start_ms"] / 1000
        end = candidate["end_ms"] / 1000
        warnings = ''.join(f"<li>{escape(item)}</li>" for item in clip.get("warnings", []))
        caption = clip.get("captions_srt")
        preview = clip.get("preview")
        media = (f'<video controls preload="metadata" src="{quote(Path(preview).name)}"></video>'
                 if preview else '<p>Preview has not been rendered.</p>')
        reasons = (f'<p>{escape(candidate["rationale"])}</p>' if candidate.get("rationale") else "")
        captions = (f'<a href="{quote(Path(caption).name)}">Captions</a> · ' if caption else "")
        heading = escape(clip["title"]) if clip.get("title") else f"Clip {index}"
        items.append(f"<article>{media}<div><h2>{heading}</h2><p>{start:.2f}–{end:.2f} s</p>"
                     f"{reasons}<ul>{warnings}</ul>{captions}"
                     f'<a href="{quote(Path(clip["edit_spec"]).name)}">Edit plan</a></div></article>')
    content = ''.join(items) or "<p>No candidates met the review criteria.</p>"
    html = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
:root{{color-scheme:dark}} body{{font:16px system-ui;margin:2rem auto;padding:0 1rem;max-width:880px;background:#15171b;color:#ebedf1}}
article{{display:flex;flex-wrap:wrap;gap:1.5rem;border-top:1px solid #484c55;padding:1.5rem 0}}
video{{width:min(260px,100%);height:auto;background:#101419;align-self:flex-start}}article div{{flex:1;min-width:240px}}
a{{color:#93d7ed}}li{{margin:.4rem 0}}
</style>
<h1>{escape(title)}</h1>
<p>Watch each cut and check the audio, captions, and framing. Use the edit plan to revise a clip.</p>
{content}</html>
"""
    write_text(path, html)


def approve(review: Path, destination: Path, *, reviewer: str, clip_index: int,
            watched: bool) -> dict:
    if not watched or not reviewer.strip():
        raise ValueError("Supply a reviewer and confirm playback before approval")
    data = json.loads(review.read_text(encoding="utf-8-sig"))
    if data.get("schema_version") != SCHEMA:
        raise ValueError("Approval needs a current review manifest")
    rows = data.get("clips", [data])
    if not 1 <= clip_index <= len(rows) <= 3:
        raise ValueError("Choose an existing review clip between 1 and 3")
    clip = rows[clip_index - 1]
    if clip.get("publish_status") != "disabled" or not clip.get("rights_ref", "").strip():
        raise ValueError("Clip is missing review or rights metadata")
    hashes = clip.get("artifact_sha256", {})
    for key in ("preview", "edit_spec", "captions_srt"):
        filename = clip.get(key)
        if not filename and key == "captions_srt":
            if hashes.get(key) is not None:
                raise ValueError("Caption artifact metadata is inconsistent")
            continue
        if not filename or not hashes.get(key):
            raise ValueError(f"Render the clip before approval: missing {key}")
        path = Path(filename)
        if digest_file(path) != hashes[key]:
            raise ValueError(f"{key} changed. Rerender and review the new revision.")
    result = {
        "schema_version": "chomp-approval/1", "manifest": str(review.resolve()),
        "manifest_sha256": digest_file(review), "job_id": data["job_id"], "clip_index": clip_index,
        "reviewer": reviewer.strip(), "approved_at": datetime.now(timezone.utc).isoformat(),
        "source_id": data["source_id"], "artifact_sha256": hashes, "rights_ref": clip["rights_ref"],
        "preview": clip["preview"], "publish_status": "disabled",
    }
    write_json(destination, result, exclusive=True)
    return result
