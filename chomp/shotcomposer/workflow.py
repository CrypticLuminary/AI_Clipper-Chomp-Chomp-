"""Local source binding, immutable Director packs and review-draft rendering."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
from html import escape
from pathlib import Path

from ..artifacts import artifact_key, digest_file, promote_new, source_identity, staged_file, write_json, write_text
from ..director.contract import check_schema, read_json, schema, validate_plan
from ..director.evidence import read_verified_bundle
from ..director.provider import evidence_view
from ..media import check_audio_track, probe, render_preview
from .capabilities import DIRECTOR_PROMPT, manifest
from .contract import validate


def source_metadata(source: Path, audio_stream: int = 0, *, meta: dict | None = None) -> dict:
    meta = probe(source) if meta is None else meta
    check_audio_track(meta, audio_stream)
    video = next((s for s in meta["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("ShotComposer requires source video")
    raw_rotation = next((float(v["rotation"]) for v in video.get("side_data_list", []) if "rotation" in v),
                        float(video.get("tags", {}).get("rotate", 0)))
    if raw_rotation % 90:
        raise ValueError("Non-quarter-turn source rotation requires a separate normalization pass")
    rotation = int(raw_rotation) % 360
    w, h = video["width"], video["height"]
    sar_text = video.get("sample_aspect_ratio", "1:1")
    sar = Fraction(sar_text.replace(":", "/")) if sar_text not in {"N/A", "0:1"} else Fraction(1)
    rate = Fraction(video["avg_frame_rate"])
    if sar <= 0 or not 1 <= rate <= 120:
        raise ValueError("Source needs a valid SAR and frame-rate rational")
    if rotation % 180:
        w, h, sar = h, w, 1 / sar
    return {"sha256": source_identity(source).removeprefix("sha256:"), "duration_ms": meta["duration_ms"],
            "width": w, "height": h, "rotation": rotation,
            "sar": {"num": sar.numerator, "den": sar.denominator},
            "frame_rate": {"num": rate.numerator, "den": rate.denominator},
            "video_stream": 0, "audio_stream": audio_stream}


def verify_source(source: Path, evidence: dict, *, meta: dict | None = None) -> None:
    check_schema(evidence, "shot_evidence")
    if source_metadata(source, evidence["source"]["audio_stream"], meta=meta) != evidence["source"]:
        raise ValueError("Source bytes, geometry, duration, frame rate or audio selection changed")


def slot(region_id: str, rect: list, *, name="main", fit="contain", required=None) -> dict:
    return {"id": name, "region_id": region_id, "rect": list(rect), "fit": fit,
            "required_region_ids": [region_id] if required is None else required,
            "crop_path": [], "zoom": [], "easing": "ease_in_out"}


def base_plan(evidence: dict, start_ms: int, end_ms: int, *, width=540, height=960, fps="30") -> dict:
    rate = Fraction(fps)
    return {"schema_version": "chomp-shot-plan/1", "source_sha256": evidence["source"]["sha256"],
            "evidence_sha256": artifact_key(evidence), "start_ms": start_ms, "end_ms": end_ms,
            "time_policy": "continuous_source", "canvas": {"width": width, "height": height,
            "fps": {"num": rate.numerator, "den": rate.denominator}, "pixel_format": "yuv420p"},
            "shots": [], "caption_exclusions": [], "capability_requests": [],
            "provenance": {"provider": "controller", "model": "none", "revision": 1,
                           "parent_sha256": None, "editorial_plan_sha256": None}}


def prepare(source: Path, out: Path, *, start_ms: int, end_ms: int, rights_ref: str,
            audio_stream=0, width=540, height=960, fps="30", evidence: dict | None = None,
            bundle_path: Path | None = None, editorial_path: Path | None = None) -> dict:
    if not rights_ref.strip():
        raise ValueError("Supply the source permission reference")
    if evidence is None:
        metadata = source_metadata(source, audio_stream)
        evidence = {"schema_version": "chomp-shot-evidence/1", "source": metadata, "rights_ref": rights_ref,
                    "regions": [{"id": "source_frame", "subject_id": "source", "role": "source",
                                 "box": [0, 0, metadata["width"], metadata["height"]],
                                 "start_ms": 0, "end_ms": metadata["duration_ms"], "support": "observed",
                                 "visibility": "visible", "basis": "source_bounds", "reviewer": None,
                                 "reference": "FFprobe display bounds only; no semantic subject verified",
                                 "minimum_output_px": 2}], "facts": [], "scene_cuts_ms": [], "tracking": []}
    else:
        verify_source(source, evidence)
        if evidence["rights_ref"] != rights_ref or evidence["source"]["audio_stream"] != audio_stream:
            raise ValueError("Evidence rights/audio selection differs from prepare arguments")
    source_region = next((r for r in evidence["regions"] if r["basis"] == "source_bounds"), None)
    plan = base_plan(evidence, start_ms, end_ms, width=width, height=height, fps=fps)
    if source_region is None:
        raise ValueError("Include a source_bounds region for the context-only starter plan")
    plan["shots"] = [{"id": "context", "start_ms": start_ms, "end_ms": end_ms, "layout": "full_source",
                      "reason": "Controller starter: preserve source context pending Director decisions",
                      "evidence_refs": [source_region["id"]],
                      "slots": [slot(source_region["id"], [0, 0, width, height])],
                      "transition": {"kind": "cut", "duration_ms": 0}, "graphics": []}]
    validate(plan, evidence)
    bundle_view, images, editorial = None, [], None
    if editorial_path is not None and bundle_path is None:
        raise ValueError("Supply the original evidence bundle with a Director storyboard")
    if bundle_path is not None:
        bundle, images = read_verified_bundle(bundle_path, source)
        if bundle["source"]["audio_stream"] != audio_stream or bundle["source"]["sha256"] != plan["source_sha256"]:
            raise ValueError("Bundle and ShotPlan need the same source and audio track")
        if not bundle["context"]["start_ms"] <= start_ms < end_ms <= bundle["context"]["end_ms"]:
            raise ValueError("Requested edit is outside the verified evidence context")
        bundle_view = evidence_view(bundle, "frames" if images else "text")
        bundle_view["bundle_sha256"] = bundle["evidence_hash"]
        for sample, data in zip(bundle_view["visual_samples"], images):
            sample.update(file=f"frames/{sample['id']}.png", sha256=sha256(data).hexdigest())
        if editorial_path is not None:
            editorial = read_json(editorial_path)
            validate_plan(editorial, bundle)
            if editorial["decision"] != "select":
                raise ValueError("A rejected or abstained storyboard does not request an edit")
            if (editorial["moment"]["start_ms"], editorial["moment"]["end_ms"]) != (start_ms, end_ms):
                raise ValueError("Use the selected storyboard's exact source range")
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "evidence.json", evidence, exclusive=True)
    write_json(out / "starter.shots.json", plan, exclusive=True)
    write_json(out / "capabilities.json", manifest(), exclusive=True)
    write_json(out / "shot-plan.schema.json", schema("shot_plan"), exclusive=True)
    write_text(out / "DIRECTOR.md", DIRECTOR_PROMPT, exclusive=True)
    if bundle_view is not None:
        write_json(out / "story-evidence.json", bundle_view, exclusive=True)
        if images:
            (out / "frames").mkdir()
            for sample, data in zip(bundle_view["visual_samples"], images):
                with (out / sample["file"]).open("xb") as handle:
                    handle.write(data)
    if editorial is not None:
        write_json(out / "storyboard.json", editorial, exclusive=True)
    write_json(out / "request.json", {"source_sha256": evidence["source"]["sha256"],
                                      "evidence_sha256": artifact_key(evidence), "start_ms": start_ms,
                                      "end_ms": end_ms, "provider_response_schema": "shot-plan.schema.json",
                                      "canvas": plan["canvas"], "evidence": "evidence.json",
                                      "story_evidence": "story-evidence.json" if bundle_view is not None else None,
                                      "storyboard": "storyboard.json" if editorial is not None else None,
                                      "visual_media_attached": bool(images)}, exclusive=True)
    return {"status": "prepared", "directory": str(out.resolve()), "verified_images": len(images),
            "review_status": "pending"}


def render(source: Path, out: Path, plan: dict, evidence: dict, *, captions: Path | None = None,
           caption_position="lower") -> dict:
    # Snapshots prevent caller mutations between validation, execution and hashing.
    plan, evidence = deepcopy(plan), deepcopy(evidence)
    result = validate(plan, evidence)
    if not result.renderable:
        return result.as_dict()
    canvas = plan["canvas"]
    if captions is not None and not plan["caption_exclusions"]:
        raise ValueError("Caption burn-in needs reviewed exclusion zones matching the selected caption placement")
    caption_bytes = captions.read_bytes() if captions is not None else None
    caption_hash = sha256(caption_bytes).hexdigest() if caption_bytes is not None else None
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "plan.json", plan, exclusive=True)
    write_json(out / "evidence.json", evidence, exclusive=True)
    if caption_bytes is not None:
        captions = out / "captions.srt"
        with captions.open("xb") as handle:
            handle.write(caption_bytes)
    report = {**result.as_dict(), "status": "rendering", "source": str(source.resolve()),
              "captions_sha256": caption_hash, "caption_position": caption_position,
              "not_rendered_requests": plan["capability_requests"]}
    write_json(out / "render.json", report, exclusive=True)
    try:
        if captions is not None:
            from ..clipwork import _caption_override

            _caption_override(captions, plan["end_ms"] - plan["start_ms"], shorts_style=True)
        with staged_file(out / "preview.mp4") as staged:
            media_report = render_preview(source, staged, plan["start_ms"], plan["end_ms"],
                                          canvas["width"], canvas["height"],
                                          audio_stream=evidence["source"]["audio_stream"],
                                          captions_path=captions, caption_position=caption_position, shorts=True,
                                          shot_plan=plan, shot_evidence=evidence)
            if captions is not None and digest_file(captions) != caption_hash:
                raise ValueError("Caption snapshot changed during rendering")
            promote_new(staged, out / "preview.mp4")
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        write_json(out / "render.json", report)
        raise
    report.update(media_report or {})
    report.update(status="rendered_pending_review", preview_sha256=digest_file(out / "preview.mp4"),
                  plan_file_sha256=digest_file(out / "plan.json"), evidence_file_sha256=digest_file(out / "evidence.json"))
    write_json(out / "render.json", report)
    from ..clipwork import SCHEMA

    # The existing approval command binds one watcher decision to these exact
    # artifacts. Native plan revisions still render through ShotComposer.
    artifacts = {"preview": out / "preview.mp4", "edit_spec": out / "plan.json",
                 "captions_srt": captions, "shot_evidence": out / "evidence.json",
                 "render_report": out / "render.json"}
    hashes = {name: digest_file(path) if path is not None else None for name, path in artifacts.items()}
    write_json(out / "review.json", {
        "schema_version": SCHEMA, "job_id": artifact_key(hashes)[:24],
        "source_id": "sha256:" + plan["source_sha256"], "rights_ref": evidence["rights_ref"],
        "review_status": "pending", "publish_status": "disabled", "artifact_sha256": hashes,
        **{name: str(path.resolve()) if path is not None else None for name, path in artifacts.items()},
    }, exclusive=True)
    rows = "".join(f"<li>{escape(s['id'])}: {s['start_ms']}–{s['end_ms']} ms — {escape(s['reason'])}</li>" for s in plan["shots"])
    pending = "".join(f"<li>{escape(r['operator'])}: {escape(r['reason'])}</li>" for r in plan["capability_requests"])
    write_text(out / "review.html", f"<!doctype html><meta charset='utf-8'><title>Director shot review</title>"
               "<style>body{background:#101419;color:#eef;font:18px system-ui;max-width:960px;margin:2em auto}"
               "video{max-height:75vh;max-width:100%}a{color:#aaf}</style><h1>Director shot review</h1>"
               "<p>Watch this draft against the source before recording playback approval.</p>"
               "<video controls src='preview.mp4'></video><ul>" + rows + "</ul><h2>Requested, not rendered</h2><ul>" + pending +
               "</ul><p><a href='plan.json'>Director plan</a> · <a href='evidence.json'>Source evidence</a> · "
               "<a href='review.json'>Approval manifest</a> · "
               "<a href='render.json'>Timing and hashes</a></p>", exclusive=True)
    return report


def revise(previous: dict, replacement: dict) -> dict:
    """Whole typed replacement is reversible by the immutable parent hash."""
    check_schema(previous, "shot_plan")
    check_schema(replacement, "shot_plan")
    revised = deepcopy(replacement)
    revised["provenance"]["revision"] = previous["provenance"]["revision"] + 1
    revised["provenance"]["parent_sha256"] = artifact_key(previous)
    return revised
