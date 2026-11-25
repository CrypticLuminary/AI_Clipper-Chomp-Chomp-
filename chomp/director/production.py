"""Opt-in deterministic export of validated Director First selections."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from ..artifacts import artifact_key, artifact_lock, digest_file, source_identity, write_json, write_text
from ..clipwork import SCHEMA
from ..editorial_selection import adapt_director_report, span
from ..editorial import validate_title
from ..manual import render_manual_preview
from ..media import check_audio_track, probe
from ..signals import read_transcript
from .campaign import resolve_campaign
from .selection import TranscriptIndex, validate_refs
from .semantic import enforce


def _verify_artifacts(clip):
    for field in ("preview", "edit_spec", "captions_srt"):
        path, expected = clip.get(field), clip.get("artifact_sha256", {}).get(field)
        if path and (not expected or digest_file(Path(path)) != expected):
            raise ValueError("Existing Director export changed; use a new output directory")
        if field in {"preview", "edit_spec"} and not path:
            raise ValueError("Director review export is missing an output artifact")


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
              "bridge_version": 1, "allow_partial": allow_partial}
    job = output.resolve() / ("director-" + artifact_key(config)[:24])
    job.mkdir(parents=True, exist_ok=True)
    manifest_path = job / "review_queue.json"
    with artifact_lock(job / ".render.lock", timeout=5):
        if manifest_path.is_file():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            for clip in existing["clips"]:
                _verify_artifacts(clip)
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
                    **{k: report[k] for k in ("source", "source_id", "duration_ms")},
                    "rights_ref": campaign.rights_ref, "clips": clips, "candidate_count": len(report["candidates"]),
                    "selected_for_review": len(clips), "manual_review_required": True, "auto_publishing": False,
                    "review_status": "pending", "director_report": str(report_path.resolve()),
                    "director_sha256": config["director_sha256"], "candidate_ledger": str(job / "candidates.json"),
                    "coverage": report.get("coverage"), "provenance": config, "campaign": asdict(campaign),
                    "review_page": str(job / "review.html"), "review_manifest": str(manifest_path)}
        write_json(job / "candidates.json", ledger)
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
