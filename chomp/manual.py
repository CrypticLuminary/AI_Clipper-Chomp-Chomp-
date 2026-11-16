"""Exact-range previews and rerendering from edited clip plans."""
from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from .artifacts import artifact_key, artifact_lock, digest_file, source_identity, write_json
from .asr import LocalTranscriber
from .clipwork import SCHEMA, create_clip, read_layout_plan, review_page
from .framing import FramingPlan, facecam_fingerprint, plan_framing
from .media import RENDER_VERSION, check_audio_track, probe
from .models import checked_span
from .reframe import cached_shorts_plan, vision_fingerprint
from .signals import read_transcript
from .sound import read_sound_cues


def render_manual_preview(
    source: Path, output_root: Path, *, rights_ref: str, start_s: float, end_s: float,
    transcript_path: Path | None = None, facecam_roi: tuple[int, int, int, int] | None = None,
    gameplay_roi: tuple[int, int, int, int] | None = None, layout: str = "auto",
    audio_stream: int = 0, facecam_model: Path | None = None, transcribe: bool = False,
    asr_model: str = "small", asr_device: str = "cpu", asr_language: str | None = None,
    profile: str = "preview", caption_position: str = "lower", audio_profile: str = "original",
    layout_plan: Path | None = None, captions_override: Path | None = None,
    expected_source_id: str | None = None, layout_segments_override: list[dict] | None = None,
    parent_edit: str | None = None,
    saliency_model: Path | None = None, camera_slot: str = "top",
    camera_motion: str = "pan", camera_fit: str = "auto",
    title: str | None = None, burn_hook: bool = False,
    saved_framing: FramingPlan | None = None, title_source: str | None = "editor",
    sound_cues_path: Path | None = None, saved_sound_cues: list[dict] | None = None,
) -> dict:
    started = perf_counter()
    if not rights_ref.strip():
        raise ValueError("A nonempty rights reference is required")
    if not math.isfinite(start_s) or not math.isfinite(end_s):
        raise ValueError("Manual preview bounds must be finite")
    source = source.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    meta = probe(source)
    start_ms, end_ms = round(start_s * 1000), round(end_s * 1000)
    duration_ms = meta["duration_ms"]
    checked_span(start_ms, end_ms, duration_ms)
    check_audio_track(meta, audio_stream)
    source_id = source_identity(source)
    if expected_source_id and source_id != expected_source_id:
        raise ValueError("Source recording changed since the edit plan was generated")
    if layout_plan is not None and layout_segments_override is not None:
        raise ValueError("Supply one layout plan")
    overrides = read_layout_plan(layout_plan) if layout_plan is not None else layout_segments_override
    mix = read_sound_cues(sound_cues_path, saved_sound_cues, duration_ms)
    transcriber = LocalTranscriber(asr_model, asr_device, asr_language) if transcribe else None
    config = {
        "schema": SCHEMA, "render_version": RENDER_VERSION, "source": str(source), "source_id": source_id,
        "bounds_ms": [start_ms, end_ms],
        "layout": layout, "facecam_roi": facecam_roi, "gameplay_roi": gameplay_roi,
        "layout_segments": overrides, "audio_stream": audio_stream, "audio_profile": audio_profile,
        "rights_ref": rights_ref, "profile": profile, "caption_position": caption_position,
        "transcript_sha256": digest_file(transcript_path) if transcript_path else None,
        "captions_override": digest_file(captions_override) if captions_override else None,
        "asr": transcriber.fingerprint if transcriber else None, "parent_edit": parent_edit,
        "camera_slot": camera_slot, "title": title, "burn_hook": burn_hook,
        "title_source": title_source if title else None,
        "saved_framing": asdict(saved_framing) if saved_framing else None,
    }
    if mix:
        config["sound_cues"] = [asdict(cue) for cue in mix]
    if layout == "shorts" and facecam_roi is None and overrides is None:
        config["vision"] = vision_fingerprint(facecam_model, saliency_model, camera_motion=camera_motion,
                                              camera_fit=camera_fit)
    elif layout == "auto-facecam" and facecam_roi is None and overrides is None:
        config["facecam_detector"] = facecam_fingerprint(facecam_model)
    job_id = "manual-" + artifact_key(config)[:24]
    output = output_root / job_id
    manifest = output / "manual_review.json"
    with artifact_lock(output_root / ".locks" / (job_id + ".lock"), timeout=5):
        if manifest.is_file():
            return {**json.loads(manifest.read_text(encoding="utf-8")), "reused_job": True}
        transcript = read_transcript(transcript_path, duration_ms)
        if transcriber:
            refined, _ = transcriber.cached(output_root / ".analysis", source, source_id, duration_ms, audio_stream,
                                            start_ms=max(0, start_ms - 3000), end_ms=min(duration_ms, end_ms + 3000),
                                            words=True)
            if any(s.start_ms < end_ms and s.end_ms > start_ms for s in refined):
                transcript = refined
        if "vision" in config:
            framing = cached_shorts_plan(source, source_id, start_ms, end_ms, output_root / ".analysis",
                                         face_model=facecam_model, saliency_model=saliency_model,
                                         camera_slot=camera_slot, fingerprint=config["vision"])
        elif overrides is None:
            framing = plan_framing(source, start_ms, end_ms, layout, facecam_roi, facecam_model,
                                   camera_slot=camera_slot)
        else:
            framing = saved_framing or FramingPlan("fit", None)
        with TemporaryDirectory(prefix=".manual-", dir=output_root, ignore_cleanup_errors=True) as tmp:
            stage = Path(tmp)
            snapshot = output / "transcript.json" if transcript else None
            snapshot_hash = None
            if snapshot:
                write_json(stage / snapshot.name, {"segments": [asdict(s) for s in transcript]})
                snapshot_hash = digest_file(stage / snapshot.name)
            clip = create_clip(
                stage, output, "manual_preview", source=source, source_id=source_id,
                rights_ref=rights_ref, start_ms=start_ms, end_ms=end_ms, duration_ms=duration_ms, transcript=transcript,
                transcript_path=snapshot, transcript_sha256=snapshot_hash,
                framing=framing, gameplay_roi=gameplay_roi, audio_stream=audio_stream, render=True,
                profile=profile, caption_position=caption_position, audio_profile=audio_profile,
                captions_override=captions_override, layout_override=overrides,
                shorts_style=layout == "shorts",
                title=title, title_source=title_source, burn_hook=burn_hook, sound_cues=mix,
            )
            report = {
                **clip, "schema_version": SCHEMA, "job_id": job_id, "source": str(source), "source_id": source_id,
                "start_ms": start_ms, "end_ms": end_ms, "duration_ms": end_ms - start_ms,
                "source_duration_ms": duration_ms, "review_page": str(output / "review.html"),
                "review_manifest": str(manifest), "manual_review_required": True, "auto_publishing": False,
                "parent_edit": parent_edit, "timing": {"total_seconds": round(perf_counter() - started, 3)},
            }
            review_page(stage / "review.html", [report], "Chomp-Chomp clip review")
            write_json(stage / manifest.name, report)
            if output.exists():
                raise FileExistsError(f"An incomplete job already exists at {output}. Choose a new output folder.")
            stage.rename(output)
        return report


def rerender(
    edit_path: Path, output_root: Path, *, start_s: float | None = None, end_s: float | None = None,
    profile: str | None = None, caption_position: str | None = None, audio_profile: str | None = None,
    layout_plan: Path | None = None, captions_override: Path | None = None,
    title: str | None = None, burn_hook: bool | None = None,
    sound_cues_path: Path | None = None,
) -> dict:
    plan = json.loads(edit_path.read_text(encoding="utf-8-sig"))
    if not isinstance(plan, dict) or plan.get("schema_version") != SCHEMA:
        raise ValueError("Rerender requires a current Chomp-Chomp edit plan")
    if (start_s is None) != (end_s is None):
        raise ValueError("Supply both updated start and end")
    start = plan["source_start_ms"] / 1000 if start_s is None else start_s
    end = plan["source_end_ms"] / 1000 if end_s is None else end_s
    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError("Manual preview bounds must be finite")
    same_bounds = (round(start * 1000) == plan["source_start_ms"]
                   and round(end * 1000) == plan["source_end_ms"])
    transcript = Path(plan["transcript_path"]) if plan.get("transcript_path") else None
    if captions_override is None and plan["caption_timing"] == "editor_supplied":
        if not same_bounds or not plan.get("captions_path"):
            raise ValueError("Supply corrected clip-local captions when changing the bounds of an edited caption track")
        captions_override = Path(plan["captions_path"])
        if digest_file(captions_override) != plan["captions_sha256"]:
            raise ValueError("The reviewed caption file changed. Pass the revised file using --captions.")
    if transcript and (not transcript.is_file() or digest_file(transcript) != plan["transcript_sha256"]):
        if captions_override is None:
            raise ValueError("Source transcript changed. Supply corrected clip-local captions.")
        transcript = None
    segments = plan["segments"]
    if layout_plan is None and isinstance(segments, list) and len(segments) == 1 and not segments[0].get("gameplay_path"):
        segments = [{**segments[0], "start_ms": round(start * 1000), "end_ms": round(end * 1000)}]
    saved_framing = None
    if layout_plan is None and same_bounds:
        roi = plan.get("facecam_roi_px")
        saved_framing = FramingPlan(plan["layout"], tuple(roi) if roi else None,
                                    plan.get("facecam_detection"), warnings=tuple(plan.get("framing_warnings", ())))
    return render_manual_preview(
        Path(plan["source"]), output_root, rights_ref=plan["rights_ref"], start_s=start, end_s=end,
        expected_source_id=plan["source_id"], transcript_path=transcript, audio_stream=plan["audio_stream"],
        profile=profile or plan["profile"], caption_position=caption_position or plan["caption_position"],
        audio_profile=audio_profile or plan["audio_profile"], captions_override=captions_override,
        layout_plan=layout_plan, layout_segments_override=segments if layout_plan is None else None,
        layout="shorts" if plan.get("shorts_style") else "auto",
        title=title if title is not None else plan.get("title"),
        title_source="editor" if title is not None else plan.get("title_source"),
        burn_hook=plan.get("burn_hook", False) if burn_hook is None else burn_hook,
        parent_edit=digest_file(edit_path), saved_framing=saved_framing,
        sound_cues_path=sound_cues_path, saved_sound_cues=plan.get("sound_cues"),
    )
