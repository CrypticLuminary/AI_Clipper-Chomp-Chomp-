"""Compare exact-source subject tracking with independent visibility labels."""
from __future__ import annotations

import argparse
from collections import Counter
from html import escape
from io import BytesIO
import json
from math import hypot
import os
from pathlib import Path
from statistics import median, mean
from urllib.parse import quote

from ..artifacts import digest_file, source_identity, write_json, write_text
from ..clipwork import layout_segments
from ..director.evidence import extract_frame
from ..subject_lock import overlap_fraction
from ..subject_view import crop_at, observation_at
from .layout_compare import layout_metrics

LABEL_SCHEMA = "chomp-subject-labels/1"
METHODS = ("hold", "pan", "subject")
ABSENT_LABELS = {"covered_by_source_overlay", "absent"}
COLORS = {"visible": "#67dec0", "partially_occluded": "#c7dca1", "covered_by_source_overlay": "#f5966b",
          "tracker_lost": "#ed6f8c", "redetected": "#4c96fa", "not_yet_observed": "#9dabc0", "unknown": "#676f82"}


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _verified_review(path: Path):
    review = _read(path)
    for name in ("preview", "edit_spec", "captions_srt"):
        filename = review[name]
        expected = review["artifact_sha256"][name]
        actual = digest_file(Path(filename)) if filename else None
        if actual != expected or (name != "captions_srt" and actual is None):
            raise ValueError(f"{name} does not match the saved review")
    return review, _read(Path(review["edit_spec"]))


def _point(box):
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _distance(a, b):
    ax, ay = _point(a)
    bx, by = _point(b)
    return hypot(ax - bx, ay - by)


def _mean(values):
    return round(mean(values), 4) if values else None


def evaluate(labels: list[dict], segments, tracking: dict | None, size,
             start: int, end: int) -> dict:
    obs = tracking["observations"] if tracking else []
    clocks = [row["at_ms"] for row in obs]
    rows, coverage, protected, errors = [], [], [], []
    false_presence = disjoint = missed = switches = 0
    for label in labels:
        t = label["at_ms"]
        truth = label.get("box")
        crop = crop_at(segments, t, size)
        actual = observation_at(obs, clocks, t)
        predicted = actual["box"] if actual else None
        fraction = round(overlap_fraction(truth, crop), 4) if truth else None
        regions = {name: round(overlap_fraction(box, crop), 4)
                   for name, box in label.get("protected_regions", {}).items()}
        protected.extend(regions.values())
        if fraction is not None:
            coverage.append(fraction)
        if tracking is not None and (actual is None or not actual["reason"].startswith("seed:")):
            if predicted and label["state"] in ABSENT_LABELS:
                false_presence += 1
            if truth and predicted:
                errors.append(_distance(truth, predicted))
                disjoint += int(overlap_fraction(truth, predicted) < .20)
                switches += int(any(overlap_fraction(predicted, box) >= .50
                                    and overlap_fraction(truth, predicted) < .20
                                    for box in label.get("distractor_boxes", [])))
            elif truth:
                missed += 1
        rows.append({"at_ms": t, "viewport_box": crop, "target_fraction": fraction, "protected_fractions": regions,
                     "state": actual["state"] if actual else None, "observed_box": predicted,
                     "actual_observation_ms": actual["at_ms"] if actual else None,
                     "confidence": actual["confidence"] if actual else None})
    boundary_jumps = [_distance(a, b) for segment in segments[1:]
                      if (a := crop_at(segments, segment.start_ms - 1, size))
                      and (b := crop_at(segments, segment.start_ms, size))]
    summary = {**layout_metrics(segments), "mean_target_fraction": _mean(coverage), "tested_visible_frames": len(coverage),
               "target_at_least_90_percent": sum(v >= .90 for v in coverage), "mean_protected_fraction": _mean(protected),
               "labelled_protected_regions": len(protected), "max_boundary_jump_px": round(max(boundary_jumps, default=0.), 2),
               "samples": rows}
    if tracking is not None:
        duration = Counter()
        for row, right in zip(obs, [*clocks[1:], end]):
            visible_through = min(row["at_ms"] + tracking["sample_step_ms"] * 2, right)
            duration[row["state"]] += max(0, visible_through - row["at_ms"])
            if right > visible_through:
                duration["between_decoded_observations"] += right - visible_through
        if clocks and clocks[0] > start:
            duration["before_first_decoded_observation"] = clocks[0] - start
        summary.update({"center_error_median_px": round(median(errors), 2) if errors else None,
                        "compared_boxes": len(errors), "wrong_location_labels": disjoint,
                        "missed_visible_labels": missed, "false_presence_labels": false_presence,
                        "tested_absent_frames": sum(row["state"] in ABSENT_LABELS for row in labels),
                        "known_distractor_matches": switches if any(row.get("distractor_boxes") for row in labels) else None,
                        "state_duration_ms": dict(duration),
                        "redetected_ms": [row["at_ms"] for row in obs if row["state"] == "redetected"],
                        "verified_seeds_ms": [row["at_ms"] for row in obs if row["reason"].startswith("seed:")]})
    return summary


def _evidence(source, previews, labels, tracked, metrics, size, start, end, out):
    from PIL import Image, ImageDraw

    frames = []
    for i, label in enumerate(labels):
        t = label["at_ms"]
        blob, decoded, _, _ = extract_frame(source, t)
        raw = Image.open(BytesIO(blob)).convert("RGB")
        raw.thumbnail((425, 252))
        card = Image.new("RGB", (945, 270), "#111822")
        card.paste(raw, (0, 22))
        draw = ImageDraw.Draw(card)
        sx, sy = raw.width / size[0], raw.height / size[1]

        def marker(box, color, width):
            if box:
                x, y, w, h = box
                draw.rectangle((round(x * sx), 22 + round(y * sy), round((x + w) * sx), 22 + round((y + h) * sy)),
                               outline=color, width=width)

        for method, color in (("hold", "#abd6ff"), ("pan", "#e8bd57"), ("subject", "#64e3b3")):
            marker(metrics[method]["samples"][i]["viewport_box"], color, 2)
        for region in label.get("protected_regions", {}).values():
            marker(region, "#eaa9ff", 2)
        marker(label.get("box"), "#ffffff", 3)
        marker(metrics["subject"]["samples"][i]["observed_box"], "#fd776c", 2)
        draw.text((5, 5), f"{decoded} ms source   {label['state']}", fill="#ffffff")
        for j, (method, path) in enumerate(zip(METHODS, previews)):
            blob, clock, _, _ = extract_frame(path, t - start)
            sample = Image.open(BytesIO(blob)).convert("RGB")
            sample.thumbnail((154, 246))
            x = 440 + 168 * j
            card.paste(sample, (x, 22))
            draw.text((x, 5), f"{method} {clock} ms", fill="#eff4fc")
            metrics[method]["samples"][i]["decoded_clip_ms"] = clock
        image = f"sample-{i + 1:03d}.jpg"
        card.save(out / image, quality=89)
        frames.append(f'<figure><img loading="lazy" src="{image}" alt="Source and three views at {decoded} ms"></figure>')

    timeline = Image.new("RGB", (945, 145), "#111822")
    axis = ImageDraw.Draw(timeline)
    samples = tracked["observations"]
    points = {}
    prior_at = None
    for index, row in enumerate(samples):
        start_x = round((row["at_ms"] - start) * 944 / (end - start))
        next_at = samples[index + 1]["at_ms"] if index + 1 < len(samples) else end
        end_x = max(start_x, round((next_at - start) * 944 / (end - start)) - 1)
        axis.rectangle((start_x, 5, end_x, 23), fill=COLORS[row["state"]])
        if row["reason"].startswith("seed:") or row["state"] == "redetected":
            axis.line((start_x, 2, start_x, 33), fill="white", width=1)
        boxes = {"Target": row["box"], **{method: crop_at(plan, row["at_ms"], size)
                                        for method, plan in metrics["segments"].items()}}
        line_colors = {"Target": "#ff8072", "hold": "#abd6ff", "pan": "#e8bd57", "subject": "#64e3b3"}
        for key, box in boxes.items():
            previous = points.get(key)
            if box:
                current = (start_x, round(133 - 94 * _point(box)[0] / size[0]))
                if previous is not None and row["at_ms"] - prior_at <= 110:
                    axis.line((*previous, *current), fill=line_colors[key], width=2)
                points[key] = current
            else:
                points[key] = None
        prior_at = row["at_ms"]
    timeline.save(out / "timeline.png")
    return "".join(frames)


def compare(hold: Path, pan: Path, subject: Path, label_path: Path, out: Path, *, cost: Path | None = None) -> dict:
    bundles = [_verified_review(path) for path in (hold, pan, subject)]
    first = bundles[0][1]
    start, end = first["source_start_ms"], first["source_end_ms"]
    source = Path(first["source"])
    labelled = _read(label_path)
    tracked = bundles[2][1]["subject_tracking"]
    labels = labelled["labels"]
    if (labelled["schema_version"] != LABEL_SCHEMA or labelled["source_id"] != first["source_id"]
            or labelled["subject_id"] != tracked["subject_id"] or source_identity(source) != first["source_id"]):
        raise ValueError("Labels, target and reviews must identify the same source and subject")
    if not labels or any(type(row["at_ms"]) is not int or not start <= row["at_ms"] < end for row in labels):
        raise ValueError("Label times must be inside the matched source interval")
    result = {"case_id": labelled["case_id"], "label_sha256": digest_file(label_path), "source_id": first["source_id"],
              "source_range_ms": [start, end], "review_status": "pending",
              "annotation_provenance": labelled["provenance"], "source_spans": tracked["source_spans"],
              "recommended_review": tracked.get("recommended_review"),
              "methods": {}, "matches_captions": len({edit["captions_sha256"] for _, edit in bundles}) == 1}
    size = tuple(tracked["source_size"])
    previews = []
    plans = {}
    for method, (review, edit) in zip(METHODS, bundles):
        fields = ("source_id", "source_start_ms", "source_end_ms", "profile", "audio_stream", "audio_profile")
        if any(edit[key] != first[key] for key in fields):
            raise ValueError("Comparisons require the same source, interval, profile and audio")
        plans[method] = layout_segments(edit["segments"], start, end)
        report = evaluate(labels, plans[method], tracked if method == "subject" else None, size, start, end)
        result["methods"][method] = {**report, "loudness": review["loudness"],
                                      "preview_sha256": review["artifact_sha256"]["preview"]}
        previews.append(Path(review["preview"]))
    if cost is not None:
        result["performance"] = _read(cost)
    out.mkdir(parents=True, exist_ok=True)
    frames = _evidence(source, previews, labels, tracked, {**result["methods"], "segments": plans}, size, start, end, out)
    write_json(out / "metrics.json", result)
    videos = []
    for method, path in zip(METHODS, previews):
        link = quote(os.path.relpath(path, out).replace(os.sep, "/"), safe="/")
        videos.append(f'<section><h2>{method}</h2><video controls preload="metadata" src="{link}"></video></section>')
    legend = " · ".join(f'<span style="color:{color}">{escape(state.replace("_", " "))}</span>'
                        for state, color in COLORS.items())
    write_text(out / "review.html",
               '<!doctype html><meta charset="utf-8"><title>SubjectLock comparison</title>'
               '<style>body{background:#111822;color:#eaf0f8;font:16px system-ui;margin:24px}main{display:flex;gap:22px;flex-wrap:wrap}'
               'video{width:230px}img{max-width:100%}figure{margin:14px 0}a{color:#ace}</style>'
               f'<h1>{escape(labelled["case_id"])}</h1><p>Source {start / 1000:g}–{end / 1000:g} s</p>'
               f'<main>{"".join(videos)}</main><p><a href="metrics.json">Measurements and verification details</a></p>'
               f'<p>{legend}</p><img src="timeline.png" alt="Visibility and horizontal target and crop paths">'
               '<p>Timeline lines show horizontal source positions. White ticks mark seeds or re-detections. '
               'White rectangles label the reference subject, coral marks the tracked box, purple marks protected regions, '
               'and blue, yellow and green show the hold, pan and SubjectLock viewports.</p>' + frames)
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    for key in (*METHODS, "labels", "out"):
        p.add_argument(f"--{key}", type=Path, required=True)
    p.add_argument("--cost", type=Path)
    args = p.parse_args()
    compare(args.hold, args.pan, args.subject, args.labels, args.out, cost=args.cost)
    print(args.out / "review.html")


if __name__ == "__main__":
    main()
