"""Plan Shorts using local detections, visible picture regions and motion over time."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import json
from math import hypot
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING

from .artifacts import artifact_key, artifact_lock, digest_file, write_json
from .camera_evidence import competing_faces, enclosing_perimeter, face_agreement, face_tracks, perimeter_support
from .camera_motion import camera_path
from .media import LayoutSegment, Rectangle, exec_checked, probe, validate_roi
from .subject_lock import SourceSpan, SubjectPlan, overlap_fraction, track_source
from .subject_view import subject_review, subject_windows

if TYPE_CHECKING:
    from .framing import FramingPlan

CAMERA_FRACTION = .30
_FACE_MODEL = Path(__file__).with_name("assets") / "face_detection_yunet_2023mar.onnx"
_SAMPLE_FPS = 4
_PLAN_REVISION = 13


@dataclass(frozen=True)
class StreamerAnchor:
    at_ms: int
    face_roi: Rectangle


def vision_fingerprint(face_model: Path | None, saliency_model: Path | None, *,
                       camera_motion: str = "pan", camera_fit: str = "auto",
                       person_model: Path | None = None, streamer_anchor: StreamerAnchor | None = None) -> dict:
    from .framing import facecam_fingerprint

    info = facecam_fingerprint(face_model or _FACE_MODEL)
    return {**info, "saliency_sha256": digest_file(saliency_model) if saliency_model else None,
            "person_model_sha256": digest_file(person_model) if person_model else None,
            "streamer_anchor": asdict(streamer_anchor) if streamer_anchor else None,
            "planner_revision": _PLAN_REVISION, "camera_motion": camera_motion, "camera_fit": camera_fit}


def cached_shorts_plan(source: Path, source_id: str, start_ms: int, end_ms: int, cache: Path, *,
                       face_model: Path | None, saliency_model: Path | None, camera_slot: str,
                       fingerprint: dict, subject_plan: SubjectPlan | None = None,
                       person_model: Path | None = None, streamer_anchor: StreamerAnchor | None = None) -> FramingPlan:
    from .framing import FramingPlan

    key = artifact_key([source_id, start_ms, end_ms, camera_slot, fingerprint])
    path = cache / "framing" / f"{key}.json"
    with artifact_lock(cache / "locks" / f"{key}.lock", timeout=300):
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            segments = tuple(LayoutSegment(**segment) for segment in data["segments"])
            return FramingPlan(data["layout"], data["facecam_roi"], data["detection"], segments,
                               tuple(data["warnings"]), data.get("subject_tracking"))
        plan = plan_shorts(source, start_ms, end_ms, face_model=face_model, saliency_model=saliency_model,
                           camera_slot=camera_slot, camera_motion=fingerprint["camera_motion"],
                           camera_fit=fingerprint["camera_fit"], subject_plan=subject_plan, person_model=person_model,
                           streamer_anchor=streamer_anchor)
        write_json(path, asdict(plan))
        return plan


@dataclass(frozen=True)
class Camera:
    face: Rectangle
    box: Rectangle
    confidence: float


@dataclass
class Sample:
    time_ms: int
    game: object
    cameras: list[Camera]
    # Face anchors use the coordinates of the downsampled gameplay image.
    faces: tuple[Rectangle, ...] = ()


def _center(rect: Rectangle) -> tuple[float, float]:
    x, y, w, h = rect
    return x + w / 2, y + h / 2


def _intersection(a: Rectangle, b: Rectangle) -> int:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))


def _iou(a: Rectangle, b: Rectangle) -> float:
    common = _intersection(a, b)
    return common / (a[2] * a[3] + b[2] * b[3] - common)


def _map(rect: Rectangle, source: tuple[int, int], target: tuple[int, int]) -> Rectangle:
    x, y, w, h = rect
    sx, sy = target[0] / source[0], target[1] / source[1]
    x0, y0 = round(x * sx), round(y * sy)
    return x0, y0, max(2, round((x + w) * sx) - x0), max(2, round((y + h) * sy) - y0)


def _edge_candidates(values, low: float, high: float, expected: float, boundary: int) -> list[tuple[int, float]]:
    lo, hi = max(1, round(low)), min(len(values), round(high))
    found = []
    while lo <= hi and len(found) < 5:
        pos = max(range(lo, hi + 1), key=lambda i: (
            values[i - 1] - .016 * abs(i - expected)
            if all(abs(i - earlier) >= 5 for earlier, _ in found) else float("-inf")
        ))
        if any(abs(pos - earlier) < 5 for earlier, _ in found):
            break
        if values[pos - 1] < 6:
            break
        found.append((pos, float(values[pos - 1])))
    if low - 2 <= boundary <= high + 2:
        found.append((boundary, 27.))
    return found


def camera_container(frame, face: Rectangle) -> Camera | None:
    """Propose a rectangle around a face for later temporal verification."""
    import numpy as np

    height, width = frame.shape[:2]
    x, y, w, h = face
    cx, cy = _center(face)
    ya, yb = max(0, y - h // 8), min(height, y + h + h // 8)
    xa, xb = max(0, x - w // 8), min(width, x + w + w // 8)
    if xb <= xa or yb <= ya:
        return None
    image = frame.astype(np.int16)
    dx = np.abs(np.diff(image, axis=1)).mean(axis=2)
    dy = np.abs(np.diff(image, axis=0)).mean(axis=2)
    vertical = dx[ya:yb].mean(axis=0)
    horizontal = dy[:, xa:xb].mean(axis=1)
    left = _edge_candidates(vertical, x - 3.5 * w, x - .09 * w, cx - 1.8 * w, 0)
    right = _edge_candidates(vertical, x + 1.09 * w, x + 4.5 * w, cx + 1.8 * w, width)
    top = _edge_candidates(horizontal, y - 2.15 * h, y - .08 * h, cy - 1.0 * h, 0)
    bottom = _edge_candidates(horizontal, y + 1.12 * h, y + 3.8 * h, cy + 1.18 * h, height)
    along_y = np.pad(dx.cumsum(axis=0), ((1, 0), (0, 0)))
    along_x = np.pad(dy.cumsum(axis=1), ((0, 0), (1, 0)))
    best: tuple[float, Rectangle, float] | None = None
    for a, ls in left:
        for b, rs in right:
            rw = b - a
            if rw < .10 * width or not 1.55 * w < rw < 7.5 * w or abs((cx - a) / rw - .5) > .32:
                continue
            for c, ts in top:
                for d, bs in bottom:
                    rh = d - c
                    if rh < .095 * height or not 1.25 * h < rh < 4.1 * h or not .49 < rw / rh < 3.15:
                        continue
                    if not .23 <= (cy - c) / rh <= .66:
                        continue
                    ls_full = (along_y[d, a-1] - along_y[c, a-1]) / rh if a else 30.
                    rs_full = (along_y[d, b-1] - along_y[c, b-1]) / rh if b < width else 30.
                    ts_full = (along_x[c-1, b] - along_x[c-1, a]) / rw if c else 30.
                    bs_full = (along_x[d-1, b] - along_x[d-1, a]) / rw if d < height else 30.
                    sides = [.30 * near + .70 * full
                             for near, full in zip((ls, rs, ts, bs), (ls_full, rs_full, ts_full, bs_full))]
                    body = sum(min(1., side / 35) for side in sides) / 4
                    balance = 1 - abs((cx - a) / rw - .5) - abs((cy - c) / rh - .46)
                    shape = 1 - .42 * abs(rw / rh - 1.48) - .16 * abs(rh / h - 2.14) - .09 * abs(rw / w - 3.8)
                    score = .65 * body + .21 * balance + .14 * shape
                    if best is None or score > best[0]:
                        best = score, (a, c, rw, rh), min(sides)
    if best is None or best[0] < .38 or best[2] < 7:
        return None
    return Camera(face, best[1], round(best[0], 3))


class ForegroundSaliency:
    """U²-Net small via OpenCV DNN. The model is only an input to crop planning."""

    def __init__(self, model: Path):
        import cv2

        self.network = cv2.dnn.readNetFromONNX(str(model))

    def __call__(self, bgr):
        import cv2
        import numpy as np

        rgb = cv2.resize(bgr, (320, 320), interpolation=cv2.INTER_AREA)[:, :, ::-1].astype(np.float32) / 255
        rgb -= np.array((.485, .456, .406), dtype=np.float32)
        rgb /= np.array((.229, .224, .225), dtype=np.float32)
        self.network.setInput(np.ascontiguousarray(rgb.transpose(2, 0, 1)[None]))
        output = np.asarray(self.network.forward(self.network.getUnconnectedOutLayersNames()[0])).squeeze()
        if output.ndim != 2 or not np.isfinite(output).all():
            raise ValueError("Expected a finite U2Netp saliency map")
        low, high = float(output.min()), float(output.max())
        probability = (output - low) / max(high - low, 1e-5)
        return cv2.resize(probability, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_LINEAR)


def _read_frames(source: Path, start: int, end: int, width: int, height: int, fps: Fraction | int):
    import numpy as np

    result = exec_checked([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror",
        "-ss", f"{start / 1000:.3f}", "-i", str(source), "-t", f"{(end - start) / 1000:.3f}",
        "-map", "0:v:0", "-an", "-vf", f"fps={fps}:start_time=0,scale={width}:{height}:flags=area",
        "-pix_fmt", "bgr24", "-threads:v", "1", "-f", "rawvideo", "pipe:1",
    ], binary=True, timeout=max(15, (end - start) / 250))
    data = np.frombuffer(result.stdout, dtype=np.uint8)
    return data.reshape(-1, height, width, 3)


def _sample(source: Path, start: int, end: int, frame_size: tuple[int, int], detector) -> tuple[list[Sample], tuple[int, int]]:
    import cv2

    width, height = frame_size
    game_size = (min(width, 336), max(2, round(height * min(width, 336) / width / 2) * 2))
    samples = []
    # Small decode batches keep the raw frame buffer bounded on longer clips.
    for block in range(start, end, 4000):
        frames = _read_frames(source, block, min(end, block + 4000), width, height, _SAMPLE_FPS)
        for index, frame in enumerate(frames):
            at = block + index * (1000 // _SAMPLE_FPS)
            if at >= end:
                break
            _, faces = detector.detect(frame) if detector is not None else (None, None)
            observations = []
            face_boxes = []
            if faces is not None:
                for row in sorted(faces, key=lambda row: float(row[-1]), reverse=True)[:4]:
                    face = tuple(round(float(v)) for v in row[:4])
                    if min(face[2:]) < 15:
                        continue
                    x, y, w, h = face
                    if x >= 0 and y >= 0 and x + w <= width and y + h <= height:
                        face_boxes.append(_map(face, frame_size, game_size))
                    camera = camera_container(frame, face)
                    if camera:
                        observations.append(camera)
            game = cv2.resize(frame, game_size, interpolation=cv2.INTER_AREA)
            samples.append(Sample(at, game, observations, tuple(face_boxes)))
    return samples, game_size


def _similar(a: Camera, b: Camera, width: int) -> bool:
    ax, ay = _center(a.face)
    bx, by = _center(b.face)
    fw, gw = a.face[2], b.face[2]
    return ((_iou(a.box, b.box) >= .45 and max(fw, gw) / min(fw, gw) <= 1.65)
            or (hypot(ax - bx, ay - by) < .064 * width and max(fw, gw) / min(fw, gw) <= 1.51))


def _track(samples: list[Sample], width: int) -> tuple[list[Camera | None], int]:
    chosen: list[Camera | None] = []
    ambiguous = 0
    last = None
    for sample in samples:
        scored = sorted(sample.cameras, key=lambda c: c.confidence + (
            .17 if last and _similar(last, c, width) else 0), reverse=True)
        if not scored:
            chosen.append(None)
            continue
        current = scored[0]
        if len(scored) > 1 and not (last and _similar(last, current, width)):
            other = scored[1]
            if current.confidence - other.confidence < .08 and _iou(current.box, other.box) < .28:
                chosen.append(None)
                ambiguous += 1
                continue
        chosen.append(current)
        last = current
    return chosen, ambiguous


def _restrict_to_anchor(samples: list[Sample], tracks, anchor: StreamerAnchor,
                        size, frame_size, game_size) -> tuple[list[Sample], dict]:
    """Limit both panel proposals and mask inference to the indicated face track."""
    index = min(range(len(samples)), key=lambda i: abs(samples[i].time_ms - anchor.at_ms))
    box = _map(anchor.face_roi, size, game_size)
    candidates = []
    if abs(samples[index].time_ms - anchor.at_ms) <= 1000 // _SAMPLE_FPS:
        candidates = [track for track in tracks if any(i == index and face_agreement(face, box) for i, face in track)]
    selected = dict(candidates[0]) if len(candidates) == 1 else {}
    restricted = []
    for i, sample in enumerate(samples):
        face = selected.get(i)
        cameras = [camera for camera in sample.cameras
                   if face is not None and face_agreement(_map(camera.face, frame_size, game_size), face)]
        restricted.append(replace(sample, cameras=cameras, faces=(face,) if face is not None else ()))
    return restricted, {**asdict(anchor), "sample_ms": samples[index].time_ms, "face_samples": len(selected)}


def _other_face_in_panel(groups, tracks, anchored, frame_size, game_size) -> bool:
    """An anchored identity is insufficient when its proposed pane contains a rival face."""
    for i, j, camera in groups:
        if camera is None:
            continue
        box = _map(camera.box, frame_size, game_size)
        for track in tracks:
            count = sum(1 for index, face in track if i <= index < j and anchored[index].faces
                        and not face_agreement(face, anchored[index].faces[0]) and overlap_fraction(face, box) >= .70)
            if count >= min(4, max(2, j - i)):
                return True
    return False


def _bridge_camera_gaps(source: Path, samples: list[Sample], tracked: list[Camera | None],
                        frame_size: tuple[int, int], game_size: tuple[int, int],
                        start: int, end: int, rate: Fraction) -> list[dict]:
    """Recover geometry only when the live image supports a continuous panel."""
    import cv2
    import numpy as np

    period_ms = 1000 // _SAMPLE_FPS
    confirmed = []
    i = 1
    while i < len(tracked) - 1:
        if tracked[i] is not None:
            i += 1
            continue
        j = i + 1
        while j < len(tracked) and tracked[j] is None:
            j += 1
        gap_start, gap_end = i, j
        i = j
        if gap_end == len(tracked) or samples[gap_end].time_ms - samples[gap_start].time_ms > 750:
            continue
        before, after = tracked[gap_start - 1], tracked[gap_end]
        if before is None or not _similar(before, after, frame_size[0]) or _iou(before.box, after.box) < .68:
            continue

        x, y, w, h = _map(before.box, frame_size, game_size)

        def signature(frame):
            region = frame[y:y + h, x:x + w]
            return cv2.resize(region, (16, 24), interpolation=cv2.INTER_AREA).astype(np.float32)

        a, b = signature(samples[gap_start - 1].game), signature(samples[gap_end].game)
        stable = np.abs(a - b).mean(axis=2) <= 15.
        if float(stable.mean()) < .40:
            continue
        reference = (a + b) * .5
        if float(reference[stable].std(axis=0).mean()) < 8.:
            continue

        def matches(frame):
            error = np.abs(signature(frame) - reference).mean(axis=2)[stable]
            return float(error.mean()) <= 12. and float((error <= 22.).mean()) >= .85

        if not all(matches(sample.game) for sample in samples[gap_start:gap_end]):
            continue
        first = samples[gap_start].time_ms
        last = samples[gap_end].time_ms
        lo, hi = max(start, first - period_ms // 2), min(end, last + period_ms // 2)
        decoded = _read_frames(source, lo, hi, *game_size, rate)
        if (len(decoded) < (hi - lo) * rate // 1000 - 1 or not len(decoded)
                or not all(matches(frame) for frame in decoded)):
            continue
        tracked[gap_start:gap_end] = [before] * (gap_end - gap_start)
        confirmed.append({"start_ms": first, "end_ms": last, "verification_frames": len(decoded)})
    return confirmed


def _groups(tracked: list[Camera | None], width: int) -> list[tuple[int, int, Camera | None]]:
    groups = []
    i = 0
    while i < len(tracked):
        first = tracked[i]
        j = i + 1
        while j < len(tracked):
            current = tracked[j]
            if (first is None and current is None) or (first and current and _similar(first, current, width)):
                j += 1
            else:
                break
        sample = [camera for camera in tracked[i:j] if camera]
        if len(sample) >= 2:
            face = tuple(round(median(values)) for values in zip(*(c.face for c in sample)))
            box = tuple(round(median(values)) for values in zip(*(c.box for c in sample)))
            chosen = Camera(face, box, median(c.confidence for c in sample))
        else:
            chosen = None
        if groups and groups[-1][2] is chosen is None:
            groups[-1] = groups[-1][0], j, None
        else:
            groups.append((i, j, chosen))
        i = j
    return groups


def _confirm_edge_panels(source: Path, groups: list[tuple[int, int, Camera | None]],
                         tracked: list[Camera | None], samples: list[Sample],
                         size: tuple[int, int], start: int, end: int, rate: Fraction, detector) -> list[dict]:
    """Confirm a recurrent camera layout when the cut trims its observations."""
    evidence = []
    period_ms = 1000 // _SAMPLE_FPS
    anchors = [camera for _, _, camera in groups if camera is not None]
    for k in sorted({0, len(groups) - 1}):
        i, j, published = groups[k]
        camera = tracked[i]
        if published is not None or camera is None or j - i != 1:
            continue
        matches = [anchor for anchor in anchors if _similar(anchor, camera, size[0])
                   and _iou(anchor.box, camera.box) >= .68]
        if not matches:
            continue
        lo = max(start, samples[i].time_ms - period_ms // 2)
        hi = min(end, samples[i].time_ms + period_ms)
        frames = _read_frames(source, lo, hi, *size, rate)
        edge = frames[:3] if i == 0 else frames[-3:]
        if len(edge) < 3:
            continue

        def face_matches(frame):
            _, faces = detector.detect(frame)
            return faces is not None and any(_iou(tuple(round(float(v)) for v in face[:4]), camera.face) >= .32
                                              for face in faces[:4])

        if all(face_matches(frame) for frame in edge):
            closest = max(matches, key=lambda anchor: _iou(anchor.box, camera.box))
            groups[k] = i, j, Camera(camera.face, closest.box, camera.confidence)
            evidence.append({"sample_ms": samples[i].time_ms, "edge": "start" if i == 0 else "end",
                             "verification_frames": len(edge)})
    return evidence


def _refine_panels(groups: list[tuple[int, int, Camera | None]], samples: list[Sample],
                   frame_size: tuple[int, int], game_size: tuple[int, int]):
    """Resolve panel seams over recurring shots, where webcam pixels vary independently of the game."""
    import numpy as np

    gw, gh = game_size
    refined = []
    cached = {}
    for i, j, camera in groups:
        if camera is None:
            refined.append((i, j, None))
            continue
        if camera.box[2] * camera.box[3] >= .45 * frame_size[0] * frame_size[1]:
            # Large panels can contain strong, stable room edges well inside
            # the true boundary. Preserve the original enclosing perimeter.
            refined.append((i, j, camera))
            continue
        related = tuple(k for k, (a, b, other) in enumerate(groups)
                        if other and _similar(camera, other, frame_size[0]))
        if related not in cached:
            anchors = [groups[k][2] for k in related]
            center_box = tuple(round(median(v)) for v in zip(*(c.box for c in anchors)))
            corner = _map(center_box, frame_size, game_size)
            x, y, w, h = corner
            fx, fy, fw, fh = _map(camera.face, frame_size, game_size)
            positions = [t for k in related for t in range(groups[k][0], groups[k][1])]
            stack = np.array([samples[t].game for t in positions[::max(1, len(positions) // 14)]], dtype=np.float32)
            if len(stack) < 3:
                cached[related] = center_box
            else:
                volatility = stack.std(axis=0).mean(axis=2)
                dx = np.abs(np.diff(stack, axis=2)).mean(axis=3)
                dy = np.abs(np.diff(stack, axis=1)).mean(axis=3)

                def seam(gradient, variability, base, radius, low, high, inside_after):
                    if base <= 1 or base >= len(variability) - 1:
                        return base
                    low, high = max(3, low, base - radius), min(len(variability) - 3, high, base + radius)
                    if low > high:
                        return base

                    def weight(pos):
                        step = float(variability[pos:pos + 3].mean() - variability[pos - 3:pos].mean())
                        outside_change = -step if inside_after else step
                        return float(gradient[pos - 1]) + .82 * max(0., outside_change) - .08 * abs(pos - base)

                    result = max(range(low, high + 1), key=weight)
                    return result if weight(result) >= 9 else base

                ay, by = max(0, y + round(.14 * h)), min(gh, y + round(.82 * h))
                gx = np.median(dx[:, ay:by].mean(axis=1), axis=0)
                vx = volatility[ay:by].mean(axis=0)
                a = seam(gx, vx, x, round(.47 * w), 0, min(fx - 2, x + round(.12 * w)), True)
                b = seam(gx, vx, x + w, round(.35 * w), max(fx + fw + 2, x + round(.88 * w)), gw, False)
                ax, bx = max(0, a + round(.12 * (b - a))), min(gw, b - round(.12 * (b - a)))
                gy = np.median(dy[:, :, ax:bx].mean(axis=2), axis=0)
                vy = volatility[:, ax:bx].mean(axis=1)
                c = seam(gy, vy, y, round(.28 * h), 0, fy - 2, True)
                d = seam(gy, vy, y + h, round(.33 * h), fy + fh + 2, gh, False)
                if b - a <= 1.22 * (d - c):
                    # A narrow container can have a long expanse of background
                    # above or below the face. Recover a strong full-width seam.
                    def outward_edge(current, low, high):
                        candidates = []
                        for pos in range(max(3, low), min(gh - 3, high) + 1):
                            edge = dy[:, pos - 1, ax:bx]
                            support = float(np.median((edge > 17.).mean(axis=1)))
                            strength = float(gy[pos - 1])
                            if support >= .55 and strength >= max(22., 1.4 * gy[current - 1]):
                                candidates.append((strength * support, pos))
                        return max(candidates)[1] if candidates else current

                    max_height = int((b - a) / .49)
                    c = outward_edge(c, max(d - max_height, y - round(.80 * h)), c - 2)
                    d = outward_edge(d, d + 2, min(c + max_height, y + round(1.65 * h)))
                valid = (b - a >= 1.55 * fw and d - c >= 1.22 * fh and .49 <= (b - a) / max(1, d - c) <= 3.15)
                cached[related] = _map((a, c, b - a, d - c), game_size, frame_size) if valid else center_box
        refined.append((i, j, Camera(camera.face, cached[related], camera.confidence)))
    return refined


def _verify_panels(source: Path, groups, refined, tracked, samples, frame_size, game_size,
                   start: int, end: int, rate: Fraction):
    """Publish only rectangles supported by a recurring, enclosing perimeter."""
    import numpy as np

    verified = []
    rejected = 0
    for (i, j, original), (_, _, adjusted) in zip(groups, refined):
        if original is None:
            verified.append((i, j, None))
            continue
        candidates = [adjusted, original]
        frames = np.stack([sample.game for sample in samples[i:j]])
        if len(frames) < 4 and (i == 0 or j == len(samples)):
            # A trim can leave too few coarse samples of a genuine opening or
            # closing panel. Require live, repeated boundaries at that edge.
            lo, hi = (start, min(end, start + 145)) if i == 0 else (max(start, end - 145), end)
            frames = _read_frames(source, lo, hi, *game_size, min(rate, 30))
            witness = tracked[i] if i == 0 else tracked[j - 1]
            candidates = [witness, *candidates]
        panel = None
        if len(frames) >= 4:
            for candidate in candidates:
                if candidate is None:
                    continue
                box = _map(candidate.box, frame_size, game_size)
                support = perimeter_support(frames, box)
                if enclosing_perimeter(support) or _panel_continues_to_frame_edge(
                    box, support, tracked[i:j], samples[i:j], frame_size, game_size
                ):
                    panel = candidate
                    break
        rejected += panel is None
        verified.append((i, j, panel))
    return verified, rejected


def _panel_continues_to_frame_edge(box, support, observations, samples, frame_size, game_size) -> bool:
    """A zoom can leave one visible seam if other frames verify the same panel."""
    import numpy as np

    interior = [i for i, value in enumerate(support) if value is not None]
    if len(interior) != 1 or support[interior[0]] < .875:
        return False
    side = interior[0]

    def coordinate(rect):
        x, y, w, h = rect
        return (x, x + w, y, y + h)[side]

    # Compare only recurrent observations on the same anchored side. A
    # full-height tile alone cannot be inferred from a single gameplay edge.
    peers = {}
    for observation, sample in zip(observations, samples):
        if observation is None:
            continue
        peer = _map(observation.box, frame_size, game_size)
        if _iou(box, peer) >= .85 and abs(coordinate(box) - coordinate(peer)) <= 2:
            peers.setdefault(peer, []).append(sample.game)
    return any(len(images) >= 4 and enclosing_perimeter(perimeter_support(np.stack(images), peer))
               for peer, images in peers.items())


def _fit(rect: Rectangle, aspect: float, center: tuple[float, float]) -> Rectangle:
    x, y, w, h = rect
    cw, ch = min(w, max(2, round(h * aspect))), min(h, max(2, round(w / aspect)))
    return (max(x, min(x + w - cw, round(center[0] - cw / 2))),
            max(y, min(y + h - ch, round(center[1] - ch / 2))), cw, ch)


def _camera_crop(camera: Camera, aspect: float) -> tuple[Rectangle, bool]:
    x, y, w, h = camera.box
    fx, fy, fw, fh = camera.face
    center = (fx + fw * .5, fy + fh * .61)
    # Keep the panel's native composition whenever it already matches the slot.
    tx, ty, tw, th = _fit(camera.box, aspect, center)
    safe = (max(x, fx - round(fw * .14)), max(y, fy - round(fh * .20)),
            round(fw * 1.28), round(fh * 1.45))
    safe = safe[0], safe[1], min(safe[2], x + w - safe[0]), min(safe[3], y + h - safe[1])
    if tw >= safe[2] and th >= safe[3]:
        sx, sy, sw, sh = safe
        tx = max(x, min(x + w - tw, max(sx + sw - tw, min(sx, tx))))
        ty = max(y, min(y + h - th, max(sy + sh - th, min(sy, ty))))
    target = tx, ty, tw, th
    return (target, False) if _intersection(target, safe) >= .96 * safe[2] * safe[3] else (camera.box, True)


def _visible_regions(picture: Rectangle, camera: Rectangle | None) -> list[Rectangle]:
    if camera is None or not _intersection(picture, camera):
        return [picture]
    x, y, w, h = picture
    ax, ay, aw, ah = camera
    options = [(x, y, min(x + w, ax) - x, h),
               (max(x, ax + aw), y, x + w - max(x, ax + aw), h),
               (x, y, w, min(y + h, ay) - y),
               (x, max(y, ay + ah), w, y + h - max(y, ay + ah))]
    return [r for r in options if min(r[2:]) >= 4]


def _heatmaps(samples: list[Sample], camera: Rectangle | None, model, frame_size: tuple[int, int]):
    import cv2
    import numpy as np

    previous = None
    learned = None
    output = []
    mask_box = _map(camera, frame_size, samples[0].game.shape[1::-1]) if camera else None
    for i, sample in enumerate(samples):
        frame = sample.game.copy()
        mask = np.ones(frame.shape[:2], dtype=np.float32)
        if mask_box:
            x, y, w, h = mask_box
            mask[y:y + h, x:x + w] = 0
            frame[y:y + h, x:x + w] = np.median(frame[mask != 0], axis=0) if np.any(mask) else 0
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        detail = cv2.GaussianBlur(cv2.convertScaleAbs(cv2.Laplacian(gray, cv2.CV_32F)), (15, 15), 0) / 255
        motion = np.zeros(gray.shape, np.float32)
        if previous is not None:
            shift, response = cv2.phaseCorrelate(previous, gray)
            delta = np.float32([[1., 0., shift[0]], [0., 1., shift[1]]])
            aligned = (cv2.warpAffine(previous, delta, gray.shape[::-1], borderMode=cv2.BORDER_REFLECT)
                       if response > .15 and max(map(abs, shift)) < gray.shape[1] * .19 else previous)
            motion = cv2.GaussianBlur(np.minimum(55., np.abs(gray - aligned)), (13, 13), 0) / 55
            # A cut is not evidence that every changed pixel is the action.
            if float(np.mean(motion)) > .56:
                motion.fill(0)
        if model is not None and i % 6 == 0:
            learned = model(frame)
        loc = np.linspace(-1, 1, gray.shape[1])
        prior = np.exp(-(loc / .49) ** 2).astype(np.float32)
        weights = .06 + .12 * detail + .32 * motion + .19 * prior
        if learned is not None:
            weights += .47 * learned
        output.append((weights * mask).astype(np.float32))
        previous = gray
    return output


def _picture_box(samples: list[Sample]) -> Rectangle:
    import cv2
    import numpy as np

    images = [cv2.cvtColor(s.game, cv2.COLOR_BGR2GRAY) for s in samples[::max(1, len(samples) // 6)]]
    valid = (np.stack(images) > 13).mean(axis=0) > .25
    h, w = valid.shape
    rows = np.flatnonzero(valid.mean(axis=1) > .12)
    cols = np.flatnonzero(valid.mean(axis=0) > .12)
    if not len(rows) or not len(cols):
        return 0, 0, w, h
    x, y = min(int(cols[0]), w // 10), min(int(rows[0]), h // 5)
    ex, ey = max(int(cols[-1]) + 1, 9 * w // 10), max(int(rows[-1]) + 1, 4 * h // 5)
    return x, y, ex - x, ey - y


def _gameplay_shots(groups: list[tuple[int, int, Camera | None]], samples: list[Sample],
                    frame_size: tuple[int, int], game_size: tuple[int, int]):
    """Recenter after a sustained visual cut without moving the output camera slot."""
    import cv2
    import numpy as np

    output = []
    for a, b, camera in groups:
        last = a
        if b - a >= 12:
            mask = np.full(game_size[::-1], 255, np.uint8)
            if camera:
                x, y, w, h = _map(camera.box, frame_size, game_size)
                mask[y:y + h, x:x + w] = 0
            histograms = []
            for sample in samples[a:b]:
                hsv = cv2.cvtColor(sample.game, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1], mask, [16, 8], [0, 180, 0, 256])
                histograms.append(cv2.normalize(hist, None, alpha=1, norm_type=cv2.NORM_L1))
            for i in range(4, len(histograms) - 4):
                before, after = histograms[i - 1], histograms[i]
                if i + a - last >= 4 and (
                    cv2.compareHist(before, after, cv2.HISTCMP_BHATTACHARYYA) >= .51
                    and cv2.compareHist(before, histograms[i + 2], cv2.HISTCMP_BHATTACHARYYA) >= .46
                    and cv2.compareHist(after, histograms[i + 2], cv2.HISTCMP_BHATTACHARYYA) < .32
                ):
                    output.append((last, i + a, camera))
                    last = i + a
        output.append((last, b, camera))
    return output if len(output) <= 20 else groups


def _action_windows(maps, picture: Rectangle, visible: list[Rectangle], aspect: float, mode: str = "pan"):
    """Select a visible crop domain once, then follow the action within it."""
    import cv2
    import numpy as np

    _, _, pw, ph = picture
    if not visible:
        return None, [], 0.
    sample_mean = np.mean(maps, axis=0)
    sample_mass = max(float(sample_mean.sum()), .001)

    def options(region):
        x, y, w, h = region
        _, _, rw, rh = _fit(region, aspect, _center(region))
        xs = list(range(x, x + w - rw + 1, 5)) + [x + w - rw]
        ys = list(range(y, y + h - rh + 1, 5)) + [y + h - rh]
        return [(a, b, rw, rh) for a in sorted(set(xs)) for b in sorted(set(ys))]

    def mass(integral, rect):
        x, y, w, h = rect
        return integral[y + h, x + w] + integral[y, x] - integral[y + h, x] - integral[y, x + w]

    total = cv2.integral(sample_mean)
    windows = [(region, options(region)) for region in visible]
    domain, allowed = max(windows, key=lambda pair: max(
        mass(total, rect) / sample_mass + .23 * rect[2] * rect[3] / (pw * ph) for rect in pair[1]
    ))

    def best_view(evidence, previous=None):
        integral = cv2.integral(evidence)
        mass_total = max(float(evidence.sum()), .001)
        active = evidence[evidence > 0]
        threshold = float(np.percentile(active, 74)) if active.size else 0.
        priority = np.maximum(0., evidence - threshold) ** 2
        priority_total = float(priority.sum())
        focus = None
        if priority_total > .0001:
            focus = (float(priority.sum(axis=0) @ np.arange(priority.shape[1])) / priority_total,
                     float(priority.sum(axis=1) @ np.arange(priority.shape[0])) / priority_total)

        def score(rect):
            x, y, w, h = rect
            value = mass(integral, rect) / mass_total
            if previous is not None:
                value -= .075 * hypot((x - previous[0]) / pw, (y - previous[1]) / ph)
            if focus is not None:
                cx, cy = _center(rect)
                value -= .18 * hypot((cx - focus[0]) / w, (cy - focus[1]) / h)
            return value

        return max(allowed, key=score)

    initial = best_view(sample_mean if mode == "hold" else np.mean(maps[:3], axis=0))
    rw, rh = initial[2:]
    if mode == "hold":
        positions = [initial[:2]] * len(maps)
    else:
        targets = [best_view(np.mean(maps[max(0, i - 2):i + 3], axis=0), initial[:2])[:2]
                   for i in range(len(maps))]
        x, y, w, h = domain
        positions = camera_path(initial[:2], targets, (rw, rh), ((x, x + w - rw), (y, y + h - rh)), mode)
    return initial, positions, (rw * rh) / max(1, pw * ph)


def _camera_switch_frame(frames, detector, prior: Camera, after: Camera) -> int | None:
    """Find where the detected face moves to the second camera geometry."""
    states = []
    for frame in frames:
        _, detections = detector.detect(frame)
        if detections is None:
            states.append(0)
            continue
        faces = [tuple(round(float(value)) for value in row[:4]) for row in detections[:4]]
        before = max((_iou(face, prior.face) for face in faces), default=0.)
        after_score = max((_iou(face, after.face) for face in faces), default=0.)
        if max(before, after_score) < .27 or abs(before - after_score) < .14:
            states.append(0)
        else:
            states.append(1 if after_score > before else -1)
    switches = [i for i in range(1, len(states) - 1)
                if states[i] == 1 and states[i - 1] != 1
                and -1 in states[max(0, i - 3):i] and 1 in states[i + 1:i + 3]]
    return min(switches, key=lambda i: states[:i].count(1) + states[i:].count(-1), default=None)


def _refine_switch(source: Path, guess: int, prior: Camera | None, after: Camera | None,
                   frame_size: tuple[int, int], start: int, end: int, rate: Fraction, detector) -> int:
    """Locate a camera-layout cut within the last sampling interval."""
    import cv2
    import numpy as np

    # Use the render's frame grid. A coarse 4 Hz sample can show the next
    # layout early, so include enough following frames to confirm the change.
    first_frame = max(0, (guess - start - 315) * rate // 1000)
    lo = start + first_frame * 1000 // rate
    hi = min(end, guess + 250)
    same_panel = prior == after
    compare_faces = prior is not None and after is not None and not same_panel
    w, h = frame_size if compare_faces else (224, max(2, round(224 * frame_size[1] / frame_size[0] / 2) * 2))
    frames = _read_frames(source, lo, hi, w, h, rate)
    switch = _camera_switch_frame(frames, detector, prior, after) if compare_faces else None
    if switch is not None:
        return start + (first_frame + switch) * 1000 // rate
    mask = np.zeros((h, w), bool)
    for camera in (prior, after):
        if camera:
            x, y, rw, rh = _map(camera.box, frame_size, (w, h))
            mask[y:y + rh, x:x + rw] ^= True
    if same_panel:
        mask.fill(True)
        if prior:
            x, y, rw, rh = _map(prior.box, frame_size, (w, h))
            mask[y:y + rh, x:x + rw] = False
    if not np.any(mask):
        return guess
    diffs = [float(cv2.absdiff(a, b)[mask].mean()) for a, b in zip(frames, frames[1:])]
    if not diffs:
        return guess
    i = max(range(len(diffs)), key=diffs.__getitem__)
    if diffs[i] < max(13, 2.15 * median(diffs)):
        return guess
    return start + (first_frame + i + 1) * 1000 // rate


def oriented_size(video: dict) -> tuple[int, int]:
    size = int(video["width"]), int(video["height"])
    rotation = next((int(d["rotation"]) for d in video.get("side_data_list", []) if "rotation" in d), 0)
    return size[::-1] if rotation % 180 == 90 else size


def plan_shorts(source: Path, start: int, end: int, *, face_model: Path | None,
                saliency_model: Path | None, camera_slot: str, camera_motion: str = "pan",
                 camera_fit: str = "auto", subject_plan: SubjectPlan | None = None,
                 person_model: Path | None = None, streamer_anchor: StreamerAnchor | None = None) -> FramingPlan:
    from .framing import FramingPlan

    def fallback(reason: str) -> FramingPlan:
        if subject_plan is not None:
            raise ValueError(f"SubjectLock could not establish a source layout. {reason}")
        return FramingPlan("letterbox", None, {"method": "shorts_fallback", "reason": reason,
                                               "source_geometry": "unknown"},
                           (LayoutSegment(start, end, "letterbox"),),
                           warnings=(reason, "Review the complete frame for essential details."))

    if camera_motion not in {"hold", "pan", "track"} or camera_fit not in {"auto", "contain"}:
        raise ValueError("Invalid Shorts camera motion or fit policy")
    if streamer_anchor is not None:
        if not start <= streamer_anchor.at_ms < end:
            raise ValueError("Streamer anchor time must be inside the selected source interval")
        if subject_plan is not None and not subject_plan.source_has_overlay:
            raise ValueError("A streamer anchor needs a source with an overlay")
    try:
        import cv2
    except ImportError:
        return fallback("Install the facecam extra to enable automatic Shorts framing.")
    if not 1000 <= end - start <= 90_000:
        return fallback("Automatic Shorts framing is available for intervals from 1 to 90 seconds.")
    meta = probe(source)
    video = next((s for s in meta["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        return fallback("The source contains no video stream.")
    size = oriented_size(video)
    if streamer_anchor is not None:
        validate_roi(streamer_anchor.face_roi, *size, "Streamer anchor")
    sar = video.get("sample_aspect_ratio", "1:1")
    if sar not in {"1:1", "N/A", "0:1", None}:
        return fallback("Confirm framing manually for non-square source pixels.")
    try:
        rate = Fraction(video["avg_frame_rate"])
        if not 20 <= rate <= 120:
            raise ValueError
    except (ValueError, ZeroDivisionError, KeyError):
        rate = Fraction(30)
    previous_threads = cv2.getNumThreads()
    cv2.setNumThreads(min(3, max(1, previous_threads)))
    try:
        return _plan(source, start, end, size, rate, face_model or _FACE_MODEL, saliency_model,
                     camera_slot, fallback, camera_motion=camera_motion, camera_fit=camera_fit,
                     subject_plan=subject_plan, person_model=person_model, streamer_anchor=streamer_anchor)
    except cv2.error as exc:
        raise ValueError("The local vision model could not analyze this source. Check the ONNX file and OpenCV.") from exc
    finally:
        cv2.setNumThreads(previous_threads)


def _plan(source, start, end, size, rate, face_model, saliency_model, camera_slot, fallback, *,
          camera_motion="pan", camera_fit="auto", subject_plan=None, person_model=None, streamer_anchor=None):
    import cv2

    from .framing import FramingPlan

    width = min(size[0], 896)
    frame_size = width, max(2, round(size[1] * width / size[0] / 2) * 2)
    detector = None
    if subject_plan is None or subject_plan.source_has_overlay:
        detector = cv2.FaceDetectorYN.create(str(face_model), "", frame_size, .72, .32, 80)
    samples, game_size = _sample(source, start, end, frame_size, detector)
    if len(samples) < 3:
        return fallback("There were too few decoded frames to track the scene.")
    faces = face_tracks(samples)
    competing = streamer_anchor is None and competing_faces(faces)
    anchor_evidence = None
    if streamer_anchor is not None:
        samples, anchor_evidence = _restrict_to_anchor(samples, faces, streamer_anchor, size, frame_size, game_size)
    track, ambiguous = _track(samples, frame_size[0])
    detected_count = sum(camera is not None for camera in track)
    bridge = _bridge_camera_gaps(source, samples, track, frame_size, game_size, start, end, rate)
    groups = _groups(track, frame_size[0])
    edge_panels = _confirm_edge_panels(source, groups, track, samples, frame_size, start, end, rate, detector)
    unstable = ambiguous >= len(samples) / 3 or len(groups) > 18
    if unstable:
        rejected = sum(camera is not None for _, _, camera in groups)
    else:
        refined = _refine_panels(groups, samples, frame_size, game_size)
        groups, rejected = _verify_panels(source, groups, refined, track, samples, frame_size,
                                           game_size, start, end, rate)
    mixed_panel = (streamer_anchor is not None and not unstable
                   and _other_face_in_panel(groups, faces, samples, frame_size, game_size))
    detection = {"method": "shorts", "detector": "YuNet", "samples": len(samples),
                 "camera_observations": detected_count, "rejected_panel_groups": rejected,
                 "streamer_anchor": anchor_evidence, "competing_face_tracks": competing,
                 "another_face_inside_anchored_panel": mixed_panel,
                 "camera_motion": camera_motion, "camera_fit": camera_fit}
    if detector is not None and (unstable or competing or mixed_panel or any(camera is None for _, _, camera in groups)):
        cutout = None
        if person_model is not None:
            from .cutout import classify_cutout

            cutout = classify_cutout(samples, person_model, game_size)
            if cutout is not None:
                cutout["face_anchor"] = _map(cutout["face_anchor"], game_size, size)
        reason = "Source geometry is unverified. Review the complete source view."
        if cutout is not None:
            reason = "The streamer appears composited over gameplay. The complete source keeps it visible once."
        elif anchor_evidence is not None and not anchor_evidence["face_samples"]:
            reason = "The streamer anchor needs one matching face in the source. Review the complete source view."
        elif mixed_panel:
            reason = "Another face shares the proposed webcam crop. Review the complete source view."
        elif competing:
            reason = "Several faces persist in the source. Mark the streamer's face to choose a camera crop."
        segments = (LayoutSegment(start, end, "letterbox"),)
        subject = None
        warnings = [reason]
        if subject_plan is not None:
            subject = track_source(source, subject_plan, (SourceSpan(start, end, None, False, 1.),), size)
            warnings.extend(subject_review(subject, segments))
        return FramingPlan("letterbox", None, {**detection, "source_geometry": "foreground_cutout" if cutout else "unknown",
                           "cutout_evidence": cutout,
                           "camera_samples": 0, "camera_containers": [], "gameplay_evidence": "source_frame",
                           "reason": reason}, segments, tuple(warnings), subject)

    groups = _gameplay_shots(groups, samples, frame_size, game_size)
    model = ForegroundSaliency(saliency_model) if saliency_model and subject_plan is None else None
    warnings = set()
    segments = []
    containers = []
    count = sum(j - i for i, j, camera in groups if camera)
    edges = [start]
    for left, right in zip(groups, groups[1:]):
        guess = samples[right[0]].time_ms
        cut = _refine_switch(source, guess, left[2], right[2], frame_size, start, end, rate, detector)
        edges.append(guess if cut - edges[-1] < 90 else cut)
    edges.append(end)
    if any(b - a < 90 for a, b in zip(edges, edges[1:])):
        return fallback("Layout changed faster than a safe automatic edit.")
    picture = _picture_box(samples)
    subject = None
    if subject_plan is not None:
        spans = []
        for k, (_, _, camera) in enumerate(groups):
            overlay = _map(camera.box, frame_size, size) if camera else None
            fraction = 1. - overlap_fraction(_map(picture, game_size, size), overlay)
            spans.append(SourceSpan(edges[k], edges[k + 1], overlay, k > 0 and camera == groups[k - 1][2],
                                    round(fraction, 3)))
        subject = track_source(source, subject_plan, tuple(spans), size)
    side_inset = camera_slot in ("left", "right")
    camera_fraction = CAMERA_FRACTION
    camera_aspect = (9 / 16) * (.43 / .75 if side_inset else 1.)
    if camera_fit == "auto" and any(
        camera is not None and _camera_crop(camera, camera_aspect / CAMERA_FRACTION)[1]
        and not _camera_crop(camera, camera_aspect / .35)[1] for _, _, camera in groups
    ):
        camera_fraction = .35
    if bridge:
        warnings.add("Review the spans where webcam pixel continuity bridged brief face detection gaps.")
    for k, (i, j, camera) in enumerate(groups):
        a, b = edges[k:k + 2]
        cam_box = None
        cam_crop = None
        fit_camera = False
        if camera:
            cam_box = camera.box
            view, fit_camera = ((cam_box, True) if camera_fit == "contain" else
                                _camera_crop(camera, camera_aspect / camera_fraction))
            cam_crop = _map(view, frame_size, size)
            source_box = _map(cam_box, frame_size, size)
            if containers and containers[-1]["end_ms"] == a and containers[-1]["source_box"] == source_box:
                containers[-1]["end_ms"] = b
            else:
                containers.append({
                    "start_ms": a, "end_ms": b, "source_box": source_box,
                    "confidence": float(camera.confidence),
                })
        mask = _map(cam_box, frame_size, game_size) if cam_box else None
        # Leave room for sampling and compression around the container edge.
        if mask:
            x, y, w, h = mask
            mask = max(0, x - 2), max(0, y - 2), w + min(2, x) + 2, h + min(2, y) + 2
        visible = _visible_regions(picture, mask)
        main_aspect = (9 / 16) / (1 - camera_fraction) if cam_box and not side_inset else 9 / 16
        if subject is None:
            maps = _heatmaps(samples[i:j], cam_box, model, frame_size)
            first, positions, coverage = _action_windows(maps, picture, visible, main_aspect, camera_motion)
        else:
            times = [max(a, min(b - 1, sample.time_ms)) for sample in samples[i:j]]
            first, positions, coverage = subject_windows(subject, times, size, game_size, picture, visible,
                                                         main_aspect, camera_motion)
        if first is None or coverage < .045:
            warnings.add("Camera covers the available gameplay. Review the source at that point.")
            segments.append(LayoutSegment(a, b, "letterbox"))
            continue
        if coverage < .18:
            warnings.add("An enlarged webcam leaves little visible gameplay in part of the cut.")
        if fit_camera and camera_fit == "auto":
            warnings.add("A camera pane uses padding to keep the whole face visible.")
        size_game = _map(first, game_size, size)[2:]
        crop = (round(positions[0][0] * size[0] / game_size[0]), round(positions[0][1] * size[1] / game_size[1]),
                *size_game)
        knots = []
        for offset, (x, y) in enumerate(positions):
            if offset % 2 and offset != len(positions) - 1:
                continue
            timestamp = a if offset == 0 else min(b, samples[i + offset].time_ms)
            xx = min(size[0] - crop[2], round(x * size[0] / game_size[0]))
            yy = min(size[1] - crop[3], round(y * size[1] / game_size[1]))
            if not knots or timestamp > knots[-1][0]:
                knots.append((timestamp, xx, yy))
        if len({(x, y) for _, x, y in knots}) == 1:
            knots = []
        if not camera:
            layout = "gameplay-only"
        else:
            layout = "facecam-gameplay"
        segments.append(LayoutSegment(a, b, layout, cam_crop, crop, fit_camera, False,
                                      tuple(knots), camera_fraction, camera_slot))

    if subject is not None:
        warnings.update(subject_review(subject, segments))
    if side_inset:
        warnings.add("Check that the camera inset leaves the important UI visible.")
    total_layout = segments[0].layout if len(segments) == 1 else "temporal"
    return FramingPlan(total_layout, segments[0].facecam_roi if len(segments) == 1 else None,
                       {**detection,
                        "source_geometry": "rectangular_webcam" if count else "no_overlay_declared",
                        "camera_samples": count, "camera_containers": containers,
                        "camera_continuity": bridge, "verified_edge_panels": edge_panels,
                        "gameplay_evidence": ("subject_lock" if subject is not None else
                                              "motion_and_u2netp" if model else "motion_and_detail")},
                       tuple(segments), tuple(sorted(warnings)), subject)
