"""Create matching-source framing evidence for two local review exports."""
from __future__ import annotations

import argparse
from html import escape
import io
import json
from math import hypot
import os
from pathlib import Path
from urllib.parse import quote

from ..artifacts import digest_file, source_identity, write_json, write_text
from ..clipwork import layout_segments
from ..director.evidence import extract_frame
from ..media import LayoutSegment, crop_position, probe


def layout_metrics(segments: tuple[LayoutSegment, ...]) -> dict:
    duration = {"split_ms": 0, "letterbox_ms": 0, "contained_camera_ms": 0}
    travel = max_velocity = max_acceleration = 0.
    reversals = 0
    for seg in segments:
        length = seg.end_ms - seg.start_ms
        if seg.layout == "letterbox":
            duration["letterbox_ms"] += length
        elif seg.layout == "facecam-gameplay":
            duration["split_ms"] += length
            duration["contained_camera_ms"] += length if seg.fit_facecam else 0
        previous_velocity = None
        previous_direction = 0
        for (t0, x0, y0), (t1, x1, y1) in zip(seg.gameplay_path, seg.gameplay_path[1:]):
            elapsed = (t1 - t0) / 1000
            distance = hypot(x1 - x0, y1 - y0)
            travel += distance
            vx, vy = (x1 - x0) / elapsed, (y1 - y0) / elapsed
            max_velocity = max(max_velocity, hypot(vx, vy))
            if previous_velocity:
                px, py, prior_time = previous_velocity
                max_acceleration = max(max_acceleration, hypot(vx - px, vy - py) / ((prior_time + elapsed) / 2))
            previous_velocity = vx, vy, elapsed
            if abs(x1 - x0) >= 3:
                direction = 1 if x1 > x0 else -1
                reversals += int(previous_direction not in (0, direction))
                previous_direction = direction
    return {
        **duration,
        "layout_changes": sum(a.layout != b.layout for a, b in zip(segments, segments[1:])),
        "camera_slot_fractions": sorted({seg.camera_fraction for seg in segments if seg.layout == "facecam-gameplay"}),
        "within_shot_crop_travel_px": round(travel, 2),
        "peak_keyframe_velocity_px_per_s": round(max_velocity, 2),
        "peak_sampled_acceleration_px_per_s2": round(max_acceleration, 2),
        "horizontal_reversals": reversals,
    }


def _annotated(source, segments, meta, clock: int, color: str):
    from PIL import ImageDraw

    image = source.copy()
    draw = ImageDraw.Draw(image)
    seg = next(s for s in segments if s.start_ms <= clock < s.end_ms)
    game = seg.gameplay_roi
    if seg.gameplay_path:
        game = (*crop_position(seg.gameplay_path, clock), *game[2:])
    sx, sy = image.width / meta[0], image.height / meta[1]
    for label, rect in (("camera crop", seg.facecam_roi), ("main crop", game)):
        if rect is None:
            continue
        x, y, w, h = rect
        left, top = round(x * sx), round(y * sy)
        draw.rectangle((left, top, round((x + w) * sx) - 1, round((y + h) * sy) - 1), outline=color, width=2)
        draw.text((left + 4, top + 3), label, fill=color, stroke_width=1, stroke_fill="#101419")
        if label == "main crop":
            cx, cy = round((x + w / 2) * sx), round((y + h / 2) * sy)
            draw.line((cx - 5, cy, cx + 5, cy), fill=color, width=2)
            draw.line((cx, cy - 5, cx, cy + 5), fill=color, width=2)
    return image, seg.layout


def compare(before: Path, after: Path, out: Path, frames_ms: list[int]) -> dict:
    from PIL import Image, ImageDraw

    reviews = [json.loads(path.read_text(encoding="utf-8-sig")) for path in (before, after)]
    for review in reviews:
        for key in ("edit_spec", "preview", "captions_srt"):
            path = review[key]
            recorded_hash = review["artifact_sha256"][key]
            if key == "captions_srt" and path is None and recorded_hash is None:
                continue
            if not path or digest_file(Path(path)) != recorded_hash:
                raise ValueError(f"The saved {key} differs from its review manifest")
    edits = [json.loads(Path(review["edit_spec"]).read_text(encoding="utf-8-sig")) for review in reviews]
    left, right = edits
    for field in ("source_id", "source_start_ms", "source_end_ms", "audio_stream", "profile"):
        if left[field] != right[field]:
            raise ValueError(f"Comparable review exports need matching {field}")
    source = Path(left["source"])
    if source_identity(source) != left["source_id"]:
        raise ValueError("The source recording differs from the stored edit")
    a, b = left["source_start_ms"], left["source_end_ms"]
    if not frames_ms or any(type(t) is not int or not a <= t < b for t in frames_ms):
        raise ValueError("Use one or more frame times inside the source interval")
    segments = [layout_segments(edit["segments"], a, b) for edit in edits]
    video = next(row for row in probe(source)["streams"] if row["codec_type"] == "video")
    size = int(video["width"]), int(video["height"])
    rotation = next((int(row["rotation"]) for row in video.get("side_data_list", []) if "rotation" in row), 0)
    if rotation % 180 == 90:
        size = size[::-1]
    paths = [Path(review["preview"]).resolve() for review in reviews]

    board = Image.new("RGB", (1140, 284 * len(frames_ms)), "#111822")
    draw = ImageDraw.Draw(board)
    frames = []
    for i, time in enumerate(frames_ms):
        source_frame, source_clock, *_ = extract_frame(source, time)
        raw = Image.open(io.BytesIO(source_frame)).convert("RGB")
        raw.thumbnail((380, 216))
        row = {"requested_source_ms": time, "decoded_source_ms": source_clock}
        for j, label in enumerate(("before", "after")):
            rendered, at, *_ = extract_frame(paths[j], time - a)
            plan_clock = min(a + at, b - 1)
            annotated, layout = _annotated(raw, segments[j], size, plan_clock, "#ffd75f" if j == 0 else "#70efc0")
            preview = Image.open(io.BytesIO(rendered)).convert("RGB")
            preview.thumbnail((156, 254))
            x, y = 570 * j, 284 * i
            board.paste(annotated, (x + 5, y + 16))
            board.paste(preview, (x + 397, y))
            draw.text((x + 5, y + 244), f"{label}  source {source_clock} ms  output {at} ms", fill="#ffffff")
            draw.text((x + 5, y + 261), layout, fill="#e5eaf0")
            row[label] = {"decoded_clip_ms": at, "plan_source_ms": plan_clock, "layout": layout}
        frames.append(row)

    out.mkdir(parents=True, exist_ok=True)
    board.save(out / "framing.png")
    report = {"source_id": left["source_id"], "source_range_ms": [a, b], "frames": frames,
              "matching_caption_assets": reviews[0]["artifact_sha256"]["captions_srt"]
              == reviews[1]["artifact_sha256"]["captions_srt"],
              "review_status": "pending"}
    tags = []
    for label, review, edit, plan, path in zip(("before", "after"), reviews, edits, segments, paths):
        report[label] = {**layout_metrics(plan), "loudness": review["loudness"],
                         "preview_sha256": review["artifact_sha256"]["preview"],
                         "render_pipeline_seconds": review.get("timing", {}).get("total_seconds"),
                         "camera_continuity": edit.get("facecam_detection", {}).get("camera_continuity", []),
                         "verified_edge_panels": edit.get("facecam_detection", {}).get("verified_edge_panels", [])}
        link = quote(os.path.relpath(path, out).replace(os.sep, "/"), safe="/")
        tags.append(f'<section><h2>{escape(label.title())}</h2><video controls preload="metadata" src="{link}"></video></section>')
    write_json(out / "metrics.json", report)
    write_text(out / "review.html",
               '<!doctype html><meta charset="utf-8"><title>Layout comparison</title>'
               '<style>body{background:#111822;color:#edf1f7;font:16px system-ui;margin:24px}'
               'main{display:flex;gap:20px;flex-wrap:wrap}video{width:240px}img{max-width:100%}'
               'a{color:#96c9ff}</style>'
               f'<h1>Source interval {a / 1000:g}–{b / 1000:g} s</h1><main>{"".join(tags)}</main>'
               '<p><a href="metrics.json">Measured layout and audio</a></p>'
               '<img src="framing.png" alt="Matched source frames with crop rectangles and rendered previews">')
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--before", type=Path, required=True, help="manual review manifest")
    p.add_argument("--after", type=Path, required=True, help="manual review manifest")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--frame-ms", type=int, required=True, action="append")
    args = p.parse_args()
    compare(args.before, args.after, args.out, args.frame_ms)
    print(args.out / "review.html")


if __name__ == "__main__":
    main()
