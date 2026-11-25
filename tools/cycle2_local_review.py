"""Replay an existing private Director evaluation using validated cache only.

No provider invocation is permitted. All footage/transcripts/results stay in
the supplied local output directory, which must be gitignored or external.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from chomp.artifacts import write_json
from chomp.director.campaign import resolve_campaign
from chomp.director.clip import run_clip
from chomp.director.production import export_selection
from chomp.director.evidence import extract_frame
from chomp.evaluation.technical import inspect_export
from chomp.media import exec_checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-report", type=Path, required=True)
    parser.add_argument("--analysis-cache", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rights-ref", required=True)
    args = parser.parse_args()
    prior = json.loads(args.prior_report.read_text(encoding="utf-8-sig"))
    if prior["campaign"]["rights_ref"] != args.rights_ref:
        raise ValueError("Use the existing evaluation authorization reference")
    if prior["provider"]["provider"] == "heuristic":
        raise ValueError("Supply the prior semantic report")
    if prior.get("status") != "review_pending":
        raise ValueError("Supply a successful historical report; failed attempts cannot be replayed")
    if args.out.exists():
        raise FileExistsError("Choose a new private output directory")
    destination = args.out.resolve()
    destination.mkdir(parents=True)
    shutil.copytree(args.analysis_cache / "director", destination / "analysis/.analysis/director")
    class CacheOnly:
        fingerprint = prior["provider"]
        def complete_structured(self, *unused, **kwargs):
            raise RuntimeError("No new provider calls are authorized by this replay command")
    campaign = resolve_campaign(**prior["campaign"])
    source = Path(prior["source"])
    reports = [run_clip(source, destination / "analysis", campaign, provider=provider,
                       section=tuple(prior["section_ms"]), transcript_path=args.transcript,
                       audio_stream=prior["audio_stream"]) for provider in (None, CacheOnly())]
    comparison = {"schema_version": "cycle2-private-review/1", "source_id": prior["source_id"],
                  "rights_reference": args.rights_ref, "new_provider_calls": reports[1]["usage"]["provider_calls"],
                  "semantic_provenance": "replayed validated historical responses; no new inference",
                  "review_status": "pending", "human_quality_comparison": None, "variants": []}
    for i, report in enumerate(reports):
        if report["status"] != "review_pending":
            raise RuntimeError(f"Private replay stopped: {report['status']}: {report.get('error')}")
        variant = f"V{i+1:02d}"
        export = export_selection(Path(report["job_report"]), destination / variant,
                                  acknowledge_permission=True, layout="shorts")
        checks = []
        for n, clip in enumerate(export["clips"], 1):
            check = inspect_export(source, Path(clip["preview"]), start_ms=clip["start_ms"], end_ms=clip["end_ms"],
                                   audio_stream=prior["audio_stream"])
            checks.append(check)
            for j, local in enumerate((0, (clip["end_ms"]-clip["start_ms"])//2), 1):
                original, _, _, _ = extract_frame(source, clip["start_ms"] + local)
                rendered, _, _, _ = extract_frame(Path(clip["preview"]), local)
                left, right = destination / "source-frame.png", destination / "output-frame.png"
                left.write_bytes(original)
                right.write_bytes(rendered)
                exec_checked(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(left), "-i", str(right),
                    "-filter_complex", "[0:v]scale=640:360,pad=640:640:0:140[left];[1:v]scale=-2:640[right];[left][right]hstack=2[out]",
                    "-map", "[out]", "-frames:v", "1", str(destination / f"{variant}-{n:02d}-{j:02d}-comparison.png")])
        comparison["variants"].append({"id": variant, "workflow": "heuristic" if i == 0 else "cached_director",
            "director_report": report["job_report"], "review_manifest": export["review_manifest"], "checks": checks})
    write_json(destination / "private-review.json", comparison)
    print(json.dumps({"report": str(destination / "private-review.json"), "variants": len(reports),
                      "new_provider_calls": comparison["new_provider_calls"]}))


if __name__ == "__main__":
    main()
