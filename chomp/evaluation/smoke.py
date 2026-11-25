"""Reproducible synthetic Cycle 2 technical checks; no AI or human quality claims."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..artifacts import digest_file, write_json
from ..director.campaign import resolve_campaign
from ..director.clip import run_clip
from ..director.production import export_selection
from ..director.provider import Response
from ..media import exec_checked, probe
from ..pipeline import process
from .benchmark import benchmark, markdown_report, validate_dataset
from .experiments import VARIANTS, compare_experiments
from .technical import inspect_export

CASES = ("landscape_speech", "no_webcam", "rectangular_corner_webcam", "webcam_switching_position",
         "multiple_visible_faces", "quiet_speech_then_laughter", "loud_gameplay_no_event", "multiple_speakers",
         "silent", "nonzero_timestamps", "multiple_audio_tracks", "variable_frame_rate", "unicode_captions",
         "empty_highlights", "corrupt_media")


class SyntheticDirector:
    fingerprint = {"provider": "synthetic", "model": "scripted-cycle2-v1"}

    def __init__(self, select=True):
        self.select = select

    def complete_structured(self, prompt, contract, *, timeout):
        payload = json.loads(prompt.split("Evidence and campaign JSON\n")[1])
        if "window" in payload:
            proposals = [{"event": "Synthetic setup and payoff", "viewer_value": "Technical verification only",
                          "evidence_ids": ["u1", "u2"]}] if self.select else []
            return Response("response_received", decision={"proposals": proposals, "leads": []})
        decisions = []
        for candidate in payload["candidates"]:
            decisions.append({"candidate_id": candidate["candidate_id"], "verdict": "select" if self.select else "reject",
                "event": "Scripted event", "viewer_value": "Technical verification only", "reason": "Synthetic provider output.",
                "audience": "Software testers", "campaign_fit": "Synthetic fixture", "confidence": "low",
                "evidence_ids": ["u1", "u2"] if self.select else [], "setup_ids": ["u1"] if self.select else [],
                "development_ids": [], "payoff_ids": ["u2"] if self.select else [],
                "title_suggestion": None, "hook_suggestion": None})
        return Response("response_received", decision={"requests": [], "decisions": decisions})


class SyntheticJudge:
    fingerprint = {"provider": "synthetic", "model": "production-script-v1"}

    def judge(self, context):
        return {"verdict": "select", "categories": ["other"],
                "evidence": {"setup": ["s0"], "event": ["s1"], "payoff": ["s1"], "reaction": []},
                "scores": {"category_payoff": 4, "standalone_clarity": 4, "hook_strength": 4, "distinctiveness": 4},
                "requires_visual_verification": True, "rationale": "Scripted synthetic decision; no human judgment."}


def make_fixture(root: Path, name: str) -> tuple[Path, Path, dict]:
    if name not in CASES:
        raise ValueError("Unknown smoke case")
    root.mkdir(parents=True, exist_ok=True)
    source = root / f"{name}.mp4"
    transcript = root / f"{name}.transcript.json"
    if name == "corrupt_media":
        source.write_bytes(b"deliberately corrupt synthetic media")
        return source, transcript, {"kind": "corrupt", "expected_failure": True}
    video = "testsrc2=size=480x270:rate=15:duration=8"
    shapes = []
    if name in {"rectangular_corner_webcam", "webcam_switching_position", "multiple_visible_faces"}:
        shapes.append("drawbox=x=20:y=20:w=100:h=80:c=red:t=fill" +
                      (":enable='lt(t,4)'" if name == "webcam_switching_position" else ""))
    if name == "webcam_switching_position":
        shapes.append("drawbox=x=350:y=170:w=100:h=80:c=red:t=fill:enable='gte(t,4)'")
    if name == "multiple_visible_faces":
        shapes.append("drawbox=x=280:y=30:w=80:h=80:c=blue:t=fill")
    if shapes:
        video += "," + ",".join(shapes)
    if name == "variable_frame_rate":
        video += ",select='lt(t,4)+not(mod(n,3))'"
    # Distinct byte/content identities keep source registration honest even
    # when two fixtures otherwise share the same tone and visual test pattern.
    marker_x = 10 + CASES.index(name) * 25
    video += f",drawbox=x={marker_x}:y=258:w=12:h=10:color=white:t=fill"
    silent = name == "silent"
    filters = exec_checked(["ffmpeg", "-hide_banner", "-filters"], timeout=10).stdout
    has_flite = " flite " in filters
    speech_case = name in {"landscape_speech", "quiet_speech_then_laughter", "multiple_speakers"}
    audio = "anullsrc=r=48000:cl=mono:d=8" if silent else "sine=frequency=880:sample_rate=48000:duration=8"
    speech_synthesized = speech_case and has_flite
    if speech_synthesized:
        audio = "flite=text='This is a synthetic setup. Here is the scripted payoff. Ha ha ha.':voice=slt,apad,atrim=duration=8"
    if name == "quiet_speech_then_laughter":
        audio += ",volume='if(lt(t,3),0.1,1)':eval=frame"
    if name == "loud_gameplay_no_event":
        audio += ",volume='if(between(t,3,5),5,0.1)':eval=frame"
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", video]
    if name == "multiple_audio_tracks":
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=8", "-f", "lavfi", "-i", audio,
                 "-map", "0:v", "-map", "1:a", "-map", "2:a"]
    else:
        args += ["-f", "lavfi", "-i", audio, "-map", "0:v", "-map", "1:a"]
    args += ["-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-fps_mode", "vfr"]
    if name == "nonzero_timestamps":
        args += ["-output_ts_offset", "5"]
    args += [str(source)]
    exec_checked(args, timeout=60)
    positive = name not in {"silent", "empty_highlights", "loud_gameplay_no_event"}
    rows = [{"start_ms": 1000, "end_ms": 2500, "text": "This is a synthetic setup.", "speaker_id": "A"},
            {"start_ms": 3000, "end_ms": 5000, "text": "यो परीक्षणको अन्तिम भाग हो।" if name == "unicode_captions" else
                "Here is the scripted payoff.", "speaker_id": "B" if name == "multiple_speakers" else "A"}]
    write_json(transcript, {"segments": rows if positive else []})
    return source, transcript, {"positive": positive, "speech_synthesized": speech_synthesized,
        "audio_stream": int(name == "multiple_audio_tracks"), "visual_semantics": "synthetic colored markers; no real face/identity claim",
        "transcript_provenance": "scripted source-clock fixture; not ASR or human annotation",
        "audio_semantics": "synthetic speech" if speech_synthesized else "silence" if silent else "tone surrogate"}


def event_annotations(source_id, duration, *, positive=True, split="development"):
    event = {"id": "synthetic-event", "event_description": "Scripted test event", "highlight_category": "synthetic",
             "core_ms": [3000, 4000], "essential_ms": [1000, 5000], "start_range_ms": [0, 1000],
             "end_range_ms": [5000, 6000], "required_audio_evidence": [], "required_visual_evidence": [],
             "necessary_context": "Synthetic setup", "viewer_value": "Technical verification only",
             "reviewer_id": "synthetic-generator", "review_status": "reviewed"}
    return {"schema_version": "chomp-source-events/1", "benchmark_name": "ChompBench-1", "source_id": source_id,
            "duration_ms": duration, "creator_id": f"synthetic-{split}", "content_category": "synthetic",
            "rights_reference": "generated synthetic fixture", "dataset_split": split,
            "annotation_completeness": "complete", "annotation_origin": "synthetic", "complete": True,
            "reviewer": "synthetic-generator (not a human)", "events": [event] if positive else []}


def run_smoke(root: Path, cases=CASES) -> dict:
    if root.exists():
        raise FileExistsError("Choose a fresh smoke directory; existing media and reports are preserved")
    root.mkdir(parents=True)
    results = []
    dataset = []
    for name in cases:
        folder = root / name
        source, transcript, info = make_fixture(folder, name)
        if name == "corrupt_media":
            try:
                probe(source)
            except (ValueError, RuntimeError) as exc:
                results.append({"case": name, "passed": True, "expected_failure": str(exc)})
                continue
            raise AssertionError("Corrupt media was unexpectedly accepted")
        campaign = resolve_campaign(rights_ref="generated synthetic fixture", min_duration=1, duration=4, max_duration=8,
                                    captions=info["positive"])
        report = run_clip(source, folder / "analysis", campaign, provider=SyntheticDirector(info["positive"]),
                          transcript_path=transcript, audio_stream=info["audio_stream"])
        if report["status"] != "review_pending":
            raise AssertionError(f"{name}: {report.get('error', report['status'])}")
        plans = {}
        if name in {"rectangular_corner_webcam", "webcam_switching_position", "multiple_visible_faces"}:
            end_first = 4000 if name == "webcam_switching_position" else 5000
            segments = [{"start_ms": 1000, "end_ms": end_first, "layout": "facecam-gameplay", "facecam_roi": [20, 20, 100, 80]}]
            if name == "webcam_switching_position":
                segments.append({"start_ms": 4000, "end_ms": 5000, "layout": "facecam-gameplay", "facecam_roi": [350, 170, 100, 80]})
            plan = folder / "reviewed-synthetic-layout.json"
            write_json(plan, {"segments": segments})
            plans["d1"] = plan
        exported = export_selection(Path(report["job_report"]), folder / "exports", acknowledge_permission=True,
                                    layout="gameplay-only", layout_plans=plans)
        checks = []
        for clip in exported["clips"]:
            check = inspect_export(source, Path(clip["preview"]), start_ms=clip["start_ms"], end_ms=clip["end_ms"],
                                   audio_stream=info["audio_stream"])
            assert (check["width"], check["height"]) == (540, 960) and not check["unexpected_silent_audio"]
            assert abs(check["duration_ms"] - (clip["end_ms"] - clip["start_ms"])) <= 150
            checks.append(check)
            exec_checked(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", clip["preview"],
                          "-vf", "fps=1,scale=180:320,tile=4x1", "-frames:v", "1", str(folder / "contact-sheet.png")])
        assert len(exported["clips"]) == int(info["positive"])
        annotations = folder / "events.json"
        split = "holdout" if name in {"no_webcam", "multiple_visible_faces"} else "development"
        gold = event_annotations(report["source_id"], report["duration_ms"], positive=info["positive"], split=split)
        write_json(annotations, gold)
        dataset.append({key: gold[key] for key in ("source_id", "creator_id", "content_category", "duration_ms",
                                                   "rights_reference", "dataset_split")})
        technical_path = folder / "technical.json"
        write_json(technical_path, {"schema_version": "chompbench-technical/1", "source_id": report["source_id"],
            "job_id": report["job_id"], "duration_ms": report["duration_ms"], "clips": [
                {"candidate_id": clip["candidate_id"], "start_ms": clip["start_ms"], "end_ms": clip["end_ms"], "measurements": check}
                for clip, check in zip(exported["clips"], checks, strict=True)]})
        metrics = benchmark(annotations, Path(report["job_report"]), manifest_path=Path(exported["review_manifest"]),
                            technical_path=technical_path)
        write_json(folder / "benchmark.json", metrics)
        (folder / "benchmark.md").write_text(markdown_report(metrics), encoding="utf-8")
        results.append({"case": name, "passed": True, "fixture": info, "director_report": report["job_report"],
                        "review_manifest": exported["review_manifest"], "checks": checks})
        if name == "landscape_speech":
            baseline = process(source, folder / "A", rights_ref=campaign.rights_ref, transcript_path=transcript,
                               min_score=0, render=True, layout="gameplay-only", audio_stream=info["audio_stream"])
            semantic = process(source, folder / "B", rights_ref=campaign.rights_ref, transcript_path=transcript,
                               min_score=0, render=True, layout="gameplay-only", editorial_provider=SyntheticJudge(),
                               audio_stream=info["audio_stream"])
            runs = {}
            for key, manifest, ledger in (
                ("A", str(Path(baseline["candidate_ledger"]).with_name("review_queue.json")), baseline["candidate_ledger"]),
                ("B", str(Path(semantic["candidate_ledger"]).with_name("review_queue.json")), semantic["candidate_ledger"]),
                ("C", None, report["job_report"]), ("D", exported["review_manifest"], exported["candidate_ledger"])):
                runs[key] = {"workflow": VARIANTS[key], "candidates": str(Path(ledger).resolve()), "manifest": manifest,
                             "rights_reference": campaign.rights_ref, "protocol_id": "synthetic-cycle2-v1",
                             "annotations_sha256": digest_file(annotations),
                             "budget": {"provider_call_limit": 0 if key == "A" else 120 if key in "CD" else 12,
                                        "candidate_limit": None, "inspected_footage_ms": 8000,
                                        "available_modalities": ["audio", "transcript"]}}
            config = folder / "matched-experiment.json"
            write_json(config, {"schema_version": "chompbench-experiment/1", "events": str(annotations.resolve()), "runs": runs})
            write_json(folder / "comparison.json", compare_experiments(config))
    result = {"schema_version": "chomp-cycle2-smoke/1", "synthetic": True, "human_review_status": "pending",
              "cases": results, "passed": len(results), "quality_claim": None}
    write_json(root / "smoke-report.json", result)
    # Synthetic split mechanics only, never an unseen real-creator quality set.
    registration = {"schema_version": "chompbench-dataset/1", "sources": dataset, "annotation_origin": "synthetic"}
    validate_dataset(registration)
    write_json(root / "dataset.json", registration)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--case", choices=CASES, action="append")
    args = parser.parse_args(argv)
    result = run_smoke(args.out.resolve(), args.case or CASES)
    print(json.dumps({"passed": result["passed"], "report": str(args.out / "smoke-report.json")}))


if __name__ == "__main__":
    main()
