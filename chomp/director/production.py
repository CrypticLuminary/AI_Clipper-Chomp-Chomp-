"""Opt-in deterministic export of validated Director First selections."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from ..artifacts import artifact_key, artifact_lock, digest_file, source_identity, write_json, write_text
from ..clipwork import SCHEMA
from ..clipwork import PROFILES
from ..captions import build_captions
from ..editorial_selection import adapt_director_report, span
from ..editorial import validate_title
from ..manual import render_manual_preview
from ..media import RENDER_VERSION, check_audio_track, probe
from ..signals import read_transcript
from .campaign import resolve_campaign
from .selection import TranscriptIndex, validate_refs
from .semantic import enforce


def _verify_artifacts(clip):
    for field in ("preview", "edit_spec", "captions_srt"):
        path, expected = clip.get(field), clip.get("artifact_sha256", {}).get(field)
        if path and not Path(path).is_file():
            raise ValueError("Director review export is missing an output artifact")
        if path and (not expected or digest_file(Path(path)) != expected):
            raise ValueError("Existing Director export changed; use a new output directory")
        if field in {"preview", "edit_spec"} and not path:
            raise ValueError("Director review export is missing an output artifact")


def _verify_cached_manifest(manifest, *, report, selected, source, transcript, job, config, campaign, ledger):
    """Reconcile the complete cache against verified controller inputs, not itself."""
    expected_header = {"schema_version": SCHEMA, "job_id": report["job_id"], "source_id": report["source_id"],
        "source": str(source), "duration_ms": report["duration_ms"], "rights_ref": campaign.rights_ref,
        "candidate_count": len(report["candidates"]), "selected_for_review": len(selected),
        "manual_review_required": True, "auto_publishing": False, "review_status": "pending",
        "director_sha256": config["director_sha256"], "provenance": config,
        "review_manifest": str(job / "review_queue.json"), "review_page": str(job / "review.html")}
    if not isinstance(manifest, dict) or any(manifest.get(k) != v or type(manifest.get(k)) is not type(v)
                                            for k, v in expected_header.items()):
        raise ValueError("Cached Director manifest source, run or counts changed")
    if artifact_key(manifest.get("campaign")) != artifact_key(asdict(campaign)):
        raise ValueError("Cached Director campaign changed")
    if Path(manifest.get("candidate_ledger", "")).resolve() != job / "candidates.json":
        raise ValueError("Cached Director ledger path changed")
    if json.loads((job / "candidates.json").read_text(encoding="utf-8")) != ledger:
        raise ValueError("Cached Director candidate ledger changed")
    clips = manifest.get("clips")
    if not isinstance(clips, list) or len(clips) != len(selected):
        raise ValueError("Cached Director manifest is missing or adding selected clips")
    by_id = {row["candidate_id"]: row for row in selected}
    seen = set()
    segments = read_transcript(transcript, report["duration_ms"]) if campaign.captions else []
    for clip in clips:
        if not isinstance(clip, dict):
            raise ValueError("Cached Director clip must be an object")
        cid = clip.get("candidate_id")
        if not isinstance(cid, str) or cid not in by_id or cid in seen:
            raise ValueError("Cached Director clip identity is missing, duplicated or unselected")
        seen.add(cid)
        row = by_id[cid]
        left, right = row["boundary"]["start_ms"], row["boundary"]["end_ms"]
        expected_clip = {"source_id": report["source_id"], "source": str(source), "start_ms": left,
            "end_ms": right, "duration_ms": right-left, "source_duration_ms": report["duration_ms"],
            "audio_stream": report["audio_stream"], "rights_ref": campaign.rights_ref,
            "manual_review_required": True, "publish_status": "disabled", "review_status": "pending"}
        if any(clip.get(k) != v or type(clip.get(k)) is not type(v) for k, v in expected_clip.items()):
            raise ValueError("Cached Director clip source, interval, track or review state changed")
        candidate = clip.get("candidate")
        if not isinstance(candidate, dict) or any(candidate.get(k) != v or type(candidate.get(k)) is not int
                for k,v in {"start_ms": left,"end_ms": right,"anchor_ms": (left+right)//2}.items()):
            raise ValueError("Cached Director candidate interval changed")
        if clip.get("editorial") != row:
            raise ValueError("Cached Director editorial decision changed")
        manual_path = Path(clip.get("review_manifest", "")).resolve()
        if not manual_path.is_relative_to(job) or manual_path.name != "manual_review.json":
            raise ValueError("Cached Director manual manifest is outside this job")
        canonical = json.loads(manual_path.read_text(encoding="utf-8"))
        for key in (*expected_clip, "job_id", "artifact_sha256", "preview", "edit_spec", "captions_srt",
                    "caption_cues", "caption_timing", "layout", "title", "title_source", "fit_facecam",
                    "facecam_roi_px", "gameplay_roi_px", "audio_mean_dbfs", "loudness"):
            if canonical.get(key) != clip.get(key):
                raise ValueError("Cached Director entry differs from its rendered manual manifest")
        captions = build_captions(segments, left, right, report["duration_ms"])
        if campaign.captions and not captions.count:
            raise ValueError("Required Director captions cannot be generated")
        if type(clip.get("caption_cues")) is not int or clip["caption_cues"] != captions.count:
            raise ValueError("Cached Director caption requirements changed")
        hashes = clip.get("artifact_sha256")
        if not isinstance(hashes, dict):
            raise ValueError("Cached Director artifact hashes are missing")
        for field, name in (("preview", "manual_preview.mp4"), ("edit_spec", "manual_preview.edit.json"),
                            ("captions_srt", "manual_preview.srt")):
            path = clip.get(field)
            if field == "captions_srt" and not captions.count:
                if path is not None or hashes.get(field) is not None:
                    raise ValueError("Unexpected cached Director captions")
                continue
            if not isinstance(path, str) or Path(path).resolve() != manual_path.parent / name:
                raise ValueError("Cached Director expected artifact path is missing or modified")
        _verify_artifacts(clip)
        edit = json.loads(Path(clip["edit_spec"]).read_text(encoding="utf-8"))
        width, height = PROFILES[config["profile"]]
        expected_edit = {"schema_version": SCHEMA, "source_id": report["source_id"], "source": str(source),
                         "source_start_ms": left, "source_end_ms": right, "audio_stream": report["audio_stream"],
                         "width": width, "height": height, "profile": config["profile"], "rights_ref": campaign.rights_ref}
        if any(edit.get(k) != v for k,v in expected_edit.items()):
            raise ValueError("Cached Director edit spec differs from the verified source interval/profile")
        if captions.count and (Path(clip["captions_srt"]).read_text(encoding="utf-8") != captions.text or
                               edit.get("captions_sha256") != hashes.get("captions_srt")):
            raise ValueError("Cached Director caption content changed")


def verify_selection(report: dict, source: Path, transcript: Path) -> tuple[dict, object]:
    """Reapply source, transcript, campaign and duplicate gates before any render."""
    ledger = adapt_director_report(report)
    if report.get("status") not in {"review_pending", "partial_review_pending"}:
        raise ValueError("Only completed editorial decisions can enter production")
    if source_identity(source) != report["source_id"]:
        raise ValueError("Director source identity mismatch")
    meta = probe(source)
    if meta["duration_ms"] != report["duration_ms"]:
        raise ValueError("Director source duration mismatch")
    check_audio_track(meta, report.get("audio_stream"))
    campaign = resolve_campaign(**report.get("campaign", {}))
    if report.get("campaign_revision") != artifact_key(asdict(campaign)):
        raise ValueError("Director campaign provenance mismatch")
    if campaign.aspect_ratio != "9:16":
        raise ValueError("Existing portrait renderer supports Director campaigns with aspect_ratio 9:16")
    expected_hash = report.get("transcript_snapshot_sha256") or report.get("transcript_input_sha256")
    if not expected_hash or digest_file(transcript) != expected_hash:
        raise ValueError("A hash-matched source-clock transcript snapshot is required")
    index = TranscriptIndex(read_transcript(transcript, report["duration_ms"]))
    known = set(index.by_id)
    for proposal in report["candidates"]:
        validate_refs(proposal.get("evidence_ids", []), known, required=False)
    for decision in report["decisions"]:
        refs = decision.get("evidence_ids", [])
        validate_refs(refs, known, required=False)
        actual = {row["id"]: row for row in index.rows(refs)}
        for item in decision.get("evidence", []):
            expected = actual.get(item["id"])
            if expected is None or any(item.get(k) != expected[k] for k in ("start_ms", "end_ms", "text", "speaker_id")):
                raise ValueError("Director evidence differs from the source transcript")
        if "heuristic_range_ms" in decision:
            if report.get("provider", {}).get("provider") != "heuristic":
                raise ValueError("Semantic selections cannot bypass evidence using heuristic bounds")
            span(decision["heuristic_range_ms"], report["duration_ms"])
    canonical = [{"evidence_ids": [], **row["editorial"]} for row in ledger["candidates"]]
    checked = enforce(report["candidates"], canonical, index, tuple(report["section_ms"]), campaign)
    original = {row["candidate_id"]: row for row in canonical}
    for row in checked:
        saved = original[row["candidate_id"]]
        if row["verdict"] != saved["verdict"]:
            raise ValueError("Director selection failed deterministic campaign or duplicate verification")
        if row["verdict"] == "select" and any(row["boundary"][k] != saved["boundary"][k] for k in ("start_ms", "end_ms")):
            raise ValueError("Director final boundaries differ from verified source evidence")
    return ledger, campaign


def export_selection(report_path: Path, output: Path, *, acknowledge_permission: bool,
                     source: Path | None = None, transcript_path: Path | None = None,
                     layout: str = "shorts", profile: str = "preview", allow_partial: bool = False,
                     layout_plans: dict[str, Path] | None = None) -> dict:
    if acknowledge_permission is not True:
        raise ValueError("Acknowledge permission before exporting Director selections")
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    source = (source or Path(report["source"])).expanduser().resolve()
    if layout not in {"shorts", "gameplay-only", "fit", "letterbox", "auto-facecam"}:
        raise ValueError("Choose an existing validated layout")
    if profile not in {"preview", "final"}:
        raise ValueError("Choose the preview or final profile")
    if report.get("status") == "partial_review_pending" and not allow_partial:
        raise ValueError("Discovery coverage is partial; pass allow_partial explicitly for a pending review export")
    transcript_path = transcript_path or Path(report.get("transcript_snapshot") or "")
    ledger, campaign = verify_selection(report, source, transcript_path)
    selected = [row for row in report["decisions"] if row["verdict"] == "select"]
    plans = layout_plans or {}
    if plans.keys() - {row["candidate_id"] for row in selected}:
        raise ValueError("Layout overrides reference an unselected candidate")
    config = {"director_sha256": digest_file(report_path), "layout": layout, "profile": profile,
              "layout_plans": {cid: digest_file(path) for cid, path in plans.items()},
              "bridge_version": 2, "render_version": RENDER_VERSION, "allow_partial": allow_partial}
    job = output.resolve() / ("director-" + artifact_key(config)[:24])
    job.mkdir(parents=True, exist_ok=True)
    manifest_path = job / "review_queue.json"
    with artifact_lock(job / ".render.lock", timeout=5):
        if manifest_path.is_file():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            _verify_cached_manifest(existing, report=report, selected=selected, source=source,
                transcript=transcript_path, job=job, config=config, campaign=campaign, ledger=ledger)
            return {**existing, "reused_job": True}
        clips = []
        for row in selected:
            boundary = row["boundary"]
            title = row.get("title_suggestion")
            title_warning = None
            if title is not None:
                try:
                    title = validate_title(title)
                except (AttributeError, TypeError, ValueError):
                    title = None
                    title_warning = "Director title suggestion requires editing before it can be used as a render title."
            clip = render_manual_preview(source, job, rights_ref=campaign.rights_ref,
                start_s=boundary["start_ms"] / 1000, end_s=boundary["end_ms"] / 1000,
                transcript_path=transcript_path if campaign.captions else None,
                expected_source_id=report["source_id"], audio_stream=report["audio_stream"],
                layout=layout, profile=profile, layout_plan=plans.get(row["candidate_id"]),
                title=title, title_source="director_suggestion", burn_hook=False)
            _verify_artifacts(clip)
            if title_warning:
                clip = {**clip, "warnings": [*clip.get("warnings", []), title_warning]}
            candidate = {"start_ms": boundary["start_ms"], "end_ms": boundary["end_ms"],
                         "anchor_ms": (boundary["start_ms"] + boundary["end_ms"]) // 2,
                         "rationale": row["reason"], "requires_visual_verification": True}
            clips.append({**clip, "candidate_id": row["candidate_id"], "candidate": candidate,
                          "editorial": row, "review_status": "pending"})
        manifest = {"schema_version": SCHEMA, "job_id": report["job_id"],
                    **{k: report[k] for k in ("source_id", "duration_ms")}, "source": str(source),
                    "rights_ref": campaign.rights_ref, "clips": clips, "candidate_count": len(report["candidates"]),
                    "selected_for_review": len(clips), "manual_review_required": True, "auto_publishing": False,
                    "review_status": "pending", "director_report": str(report_path.resolve()),
                    "director_sha256": config["director_sha256"], "candidate_ledger": str(job / "candidates.json"),
                    "coverage": report.get("coverage"), "provenance": config, "campaign": asdict(campaign),
                    "review_page": str(job / "review.html"), "review_manifest": str(manifest_path)}
        write_json(job / "candidates.json", ledger)
        _verify_cached_manifest(manifest, report=report, selected=selected, source=source,
            transcript=transcript_path, job=job, config=config, campaign=campaign, ledger=ledger)
        # Each manual job owns its media files; links include their relative directory.
        from html import escape
        cards = []
        for clip in clips:
            relative = Path(clip["preview"]).relative_to(job).as_posix()
            cards.append(f'<p>{escape(clip["candidate_id"])}</p><video controls src="{escape(relative, quote=True)}"></video>')
        write_text(job / "review.html", '<!doctype html><meta charset="utf-8"><h1>Director export: human review pending</h1>' +
                   '<p>Check the source, audio, captions and framing before approval.</p>' + "".join(cards))
        write_json(manifest_path, manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--acknowledge-permission", action="store_true", required=True)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--layout", default="shorts")
    parser.add_argument("--profile", default="preview", choices=("preview", "final"))
    args = parser.parse_args(argv)
    try:
        result = export_selection(args.report, args.output, acknowledge_permission=args.acknowledge_permission,
                                  source=args.source, transcript_path=args.transcript, layout=args.layout,
                                  profile=args.profile, allow_partial=args.allow_partial)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
