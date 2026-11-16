"""Source-clock observations for one explicitly selected visual subject."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
import json
from math import hypot
from pathlib import Path

from .media import Rectangle

SEED_SCHEMA = "chomp-subject-seed/1"
TRACK_SCHEMA = "chomp-subject-track/1"
TRACK_REVISION = 3
STEP_MS = 50


@dataclass(frozen=True)
class Seed:
    at_ms: int
    box: Rectangle
    origin: str


@dataclass(frozen=True)
class ProtectedRegion:
    id: str
    start_ms: int
    end_ms: int
    box: Rectangle


@dataclass(frozen=True)
class SubjectPlan:
    source_id: str
    subject_id: str
    seeds: tuple[Seed, ...]
    protected_regions: tuple[ProtectedRegion, ...]
    source_has_overlay: bool


@dataclass(frozen=True)
class SourceSpan:
    start_ms: int
    end_ms: int
    overlay: Rectangle | None
    cut: bool = False
    usable_fraction: float = 1.


@dataclass(frozen=True)
class Observation:
    at_ms: int
    state: str
    confidence: float
    box: Rectangle | None
    reason: str


def _fields(row, required, optional=()):
    return isinstance(row, dict) and set(required) <= row.keys() <= set(required) | set(optional)


def _valid_box(value, size):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(v) is not int for v in value)):
        raise ValueError("Subject and protected boxes require four integer source pixels")
    x, y, w, h = value
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > size[0] or y + h > size[1]:
        raise ValueError("Subject or protected box is outside the oriented source")
    return x, y, w, h


def read_subject_plan(path: Path, source_id: str, start: int, end: int, size: tuple[int, int]) -> SubjectPlan:
    row = json.loads(path.read_text(encoding="utf-8-sig"))
    if (not _fields(row, ("schema_version", "source_id", "subject_id", "seeds"),
                    ("protected_regions", "source_has_overlay"))
            or row["schema_version"] != SEED_SCHEMA or row["source_id"] != source_id):
        raise ValueError("Subject plan must use chomp-subject-seed/1 and match the source SHA-256")
    subject_id = row["subject_id"]
    overlays = row.get("source_has_overlay", True)
    if (not isinstance(subject_id, str) or not subject_id.strip() or len(subject_id) > 128
            or type(overlays) is not bool or not isinstance(row["seeds"], list) or not row["seeds"]):
        raise ValueError("Supply a subject ID, one or more seeds and a boolean source_has_overlay")
    seeds = []
    for item in row["seeds"]:
        if (not _fields(item, ("at_ms",), ("point", "box", "origin")) or ("point" in item) == ("box" in item)
                or type(item["at_ms"]) is not int or not start <= item["at_ms"] < end):
            raise ValueError("Each seed needs one box or point and a source time inside the clip")
        origin = item.get("origin", "human")
        if not isinstance(origin, str) or not origin.strip() or len(origin) > 128:
            raise ValueError("Each seed needs a short provenance label")
        if "box" in item:
            box = _valid_box(item["box"], size)
        else:
            point = item["point"]
            if (not isinstance(point, list) or len(point) != 2 or any(type(v) is not int for v in point)
                    or not all(0 <= v < max_v for v, max_v in zip(point, size))):
                raise ValueError("Seed point is outside the oriented source")
            radius = max(6, round(min(size) * .024))
            x, y = max(0, point[0] - radius), max(0, point[1] - radius)
            box = x, y, min(size[0], point[0] + radius + 1) - x, min(size[1], point[1] + radius + 1) - y
        if seeds and item["at_ms"] <= seeds[-1].at_ms:
            raise ValueError("Subject seed times must be unique and increasing")
        seeds.append(Seed(item["at_ms"], box, origin))
    regions = []
    items = row.get("protected_regions", [])
    if not isinstance(items, list):
        raise ValueError("Protected regions must be an array")
    for item in items:
        if not _fields(item, ("id", "box"), ("start_ms", "end_ms")):
            raise ValueError("Protected regions need an ID and source box")
        name, a, b = item["id"], item.get("start_ms", start), item.get("end_ms", end)
        if (not isinstance(name, str) or not name.strip() or len(name) > 128 or any(r.id == name for r in regions)
                or type(a) is not int or type(b) is not int or not start <= a < b <= end):
            raise ValueError("Protected regions need unique IDs and a range inside the clip")
        regions.append(ProtectedRegion(name, a, b, _valid_box(item["box"], size)))
    return SubjectPlan(source_id, subject_id, tuple(seeds), tuple(regions), overlays)


def overlap_fraction(box: Rectangle, region: Rectangle | None) -> float:
    if region is None:
        return 0.
    x, y, w, h = box
    a, b, rw, rh = region
    return max(0, min(x + w, a + rw) - max(x, a)) * max(0, min(y + h, b + rh) - max(y, b)) / (w * h)


def _scaled(box: Rectangle, src: tuple[int, int], dst: tuple[int, int]) -> Rectangle:
    x, y, w, h = box
    left, top = round(x * dst[0] / src[0]), round(y * dst[1] / src[1])
    right, bottom = round((x + w) * dst[0] / src[0]), round((y + h) * dst[1] / src[1])
    left, top = min(left, dst[0] - 1), min(top, dst[1] - 1)
    return left, top, max(1, right - left), max(1, bottom - top)


def _frames(source: Path, start: int, end: int, size: tuple[int, int], required: list[int]):
    import cv2

    video = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG)
    try:
        if (not video.isOpened() or (start and not video.set(cv2.CAP_PROP_POS_MSEC, start))):
            raise ValueError("Could not seek the source for SubjectLock")
        due, index, previous = start, 0, -1
        while video.grab():
            at = round(video.get(cv2.CAP_PROP_POS_MSEC))
            if at < previous or (at == previous and at == 0):
                raise ValueError("SubjectLock requires monotone decoded source timestamps")
            previous = at
            if at < start:
                continue
            if at >= end:
                break
            if at < due and (index >= len(required) or at < required[index]):
                continue
            ok, frame = video.retrieve()
            if not ok:
                raise ValueError(f"Could not decode the subject observation at {at} ms")
            if abs(frame.shape[1] / frame.shape[0] - size[0] / size[1]) > .025:
                raise ValueError("SubjectLock decoded unexpected source orientation")
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA) if gray.shape[::-1] != size else gray
            yield at, gray
            due = start + ((at - start) // STEP_MS + 1) * STEP_MS
            index = bisect_right(required, at, lo=index)
    finally:
        video.release()


def _patch(gray, box):
    x, y, w, h = box
    return gray[y:y + h, x:x + w]


def _points(gray, box, overlay):
    import cv2
    import numpy as np

    x, y, w, h = box
    dx, dy = w // 9, h // 9
    view = gray[y + dy:y + h - dy, x + dx:x + w - dx]
    if min(view.shape) < 5:
        return None
    mask = None
    if overlap_fraction(box, overlay):
        ax, ay, aw, ah = overlay
        left, top = max(0, ax - x - dx), max(0, ay - y - dy)
        right, bottom = max(0, ax + aw - x - dx), max(0, ay + ah - y - dy)
        mask = np.full(view.shape, 255, np.uint8)
        mask[top:bottom, left:right] = 0
    points = cv2.goodFeaturesToTrack(view, maxCorners=72, qualityLevel=.016, minDistance=3, blockSize=3, mask=mask)
    return points + np.float32([x + dx, y + dy]) if points is not None else None


def _similarity(first, second):
    import cv2

    a = cv2.resize(first, (32, 32), interpolation=cv2.INTER_AREA)
    b = cv2.resize(second, (32, 32), interpolation=cv2.INTER_AREA)
    if min(float(a.std()), float(b.std())) < 7:
        return 0.
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0])


def _flow(previous, current, points, box, overlay):
    import cv2
    import numpy as np

    if points is None or len(points) < 5:
        return None
    options = {"winSize": (17, 17), "maxLevel": 3}
    moved, good, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, None, **options)
    if moved is None:
        return None
    returned, back, _ = cv2.calcOpticalFlowPyrLK(current, previous, moved, None, **options)
    if returned is None:
        return None
    old, new = points[:, 0], moved[:, 0]
    accepted = (good[:, 0] != 0) & (back[:, 0] != 0) & (np.linalg.norm(returned[:, 0] - old, axis=1) <= 1.25)
    if overlay:
        ax, ay, aw, ah = overlay
        accepted &= ~((ax <= new[:, 0]) & (new[:, 0] < ax + aw) & (ay <= new[:, 1]) & (new[:, 1] < ay + ah))
    if int(accepted.sum()) < 5:
        return None
    old, new = old[accepted], new[accepted]
    affine, inliers = cv2.estimateAffinePartial2D(old, new, method=cv2.RANSAC,
                                                ransacReprojThreshold=2., maxIters=120, refineIters=5)
    if affine is None or int(inliers.sum()) < max(5, len(old) * .62, len(points) * .23):
        return None
    supported = (inliers[:, 0] != 0)
    x, y, w, h = box
    support_width, support_height = np.ptp(old[supported], axis=0)
    scale = hypot(affine[0, 0], affine[1, 0])
    if support_width < .19 * w or support_height < .23 * h or not .80 <= scale <= 1.23:
        return None
    cx, cy = affine @ np.array([x + w / 2, y + h / 2, 1.])
    bw, bh = round(w * scale), round(h * scale)
    left, top = round(cx - bw / 2), round(cy - bh / 2)
    right, bottom = min(current.shape[1], left + bw), min(current.shape[0], top + bh)
    bx, by = max(0, left), max(0, top)
    if right - bx < .65 * w or bottom - by < .65 * h:
        return None
    candidate = bx, by, right - bx, bottom - by
    appearance = _similarity(_patch(previous, box), _patch(current, candidate))
    if overlap_fraction(candidate, overlay) > .30 or appearance < .55:
        return None
    confidence = round(.5 * appearance + .5 * float(inliers.mean()), 3)
    clipped = (right - bx) * (bottom - by) < .93 * bw * bh
    return candidate, new[supported].reshape(-1, 1, 2), confidence, clipped


def _find_patch(gray, templates, last_box, overlay, wide):
    import cv2
    import numpy as np

    x, y, w, h = last_box
    height, width = gray.shape
    radius = max(w, h) * 2
    left, top = (0, 0) if wide else (max(0, x - radius), max(0, y - radius))
    right, bottom = (width, height) if wide else (min(width, x + w + radius), min(height, y + h + radius))
    search = gray[top:bottom, left:right]
    matches = []
    for exemplar in templates:
        if min(exemplar.shape) < 8 or float(exemplar.std()) < 9:
            continue
        for scale in (1., .83, 1.2):
            bw, bh = max(8, round(exemplar.shape[1] * scale)), max(8, round(exemplar.shape[0] * scale))
            if bh > search.shape[0] or bw > search.shape[1]:
                continue
            template = cv2.resize(exemplar, (bw, bh), interpolation=cv2.INTER_LINEAR)
            scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
            if overlay:
                ax, ay, aw, ah = overlay
                xx = np.arange(scores.shape[1]) + left
                yy = np.arange(scores.shape[0]) + top
                columns = np.maximum(0, np.minimum(xx + bw, ax + aw) - np.maximum(xx, ax))
                rows = np.maximum(0, np.minimum(yy + bh, ay + ah) - np.maximum(yy, ay))
                scores[rows[:, None] * columns > .22 * bw * bh] = -1
            _, best, _, (mx, my) = cv2.minMaxLoc(scores)
            limit = .80 if wide else .76
            if best < limit:
                continue
            scores[max(0, my - bh // 2):my + bh // 2 + 1, max(0, mx - bw // 2):mx + bw // 2 + 1] = -1
            runner_up = float(scores.max())
            matches.append((best, runner_up, (mx + left, my + top, bw, bh)))
    if not matches:
        return None
    best, second, candidate = max(matches)
    alternatives = (score for score, _, other in matches if overlap_fraction(candidate, other) < .48)
    if best - max(second, max(alternatives, default=-1.)) < (.055 if wide else .045):
        return None
    return candidate, round(best, 3)


def follow_subject(frames, plan: SubjectPlan, spans: tuple[SourceSpan, ...], source_size, sample_size):
    """Accept confirmed observations only. Keep unavailable rows free of invented boxes."""
    observations = []
    index = seed_index = 0
    previous_index = -1
    previous_frame = points = box = anchor = latest = pending = None
    last_seen = lost_since = next_probe = 0
    attempts = 0
    covered = crossed_cut = False
    for at, gray in frames:
        while index + 1 < len(spans) and at >= spans[index].end_ms:
            index += 1
        span = spans[index]
        overlay = _scaled(span.overlay, source_size, sample_size) if span.overlay else None
        if index != previous_index and span.cut:
            previous_frame, pending = None, None
            crossed_cut = True
            covered = False
        previous_index = index
        seed = None
        while seed_index < len(plan.seeds) and plan.seeds[seed_index].at_ms <= at:
            seed = plan.seeds[seed_index]
            seed_index += 1
        proposal = None
        manual = False
        reason = "flow_consensus"
        if seed is not None and at - seed.at_ms <= 100:
            candidate = _scaled(seed.box, source_size, sample_size)
            if overlap_fraction(candidate, overlay) <= .30:
                proposal = candidate, _points(gray, candidate, overlay), 1., False
                reason, manual = f"seed:{seed.origin}", True
        elif previous_frame is not None and at - last_seen <= 125 and not crossed_cut:
            proposal = _flow(previous_frame, gray, points, box, overlay)
        if proposal is None and box is not None and not crossed_cut:
            obscured = overlap_fraction(box, overlay) >= .65
            was_covered = covered
            covered = obscured and (covered or at - last_seen <= 500)
            if (covered and not was_covered) or (was_covered and not covered) or previous_frame is not None:
                lost_since, attempts, next_probe, pending = at, 0, at, None
            if at >= next_probe and attempts < 12 and at - lost_since <= 2250:
                wide = attempts >= 3 or covered
                templates = (anchor,) if latest is anchor else (anchor, latest)
                found = _find_patch(gray, templates, box, overlay, wide)
                attempts += 1
                next_probe = at + 95
                if found is not None and pending is not None and overlap_fraction(found[0], pending) >= .55:
                    candidate, score = found
                    proposal = candidate, _points(gray, candidate, overlay), score, False
                    reason = "confirmed_image_match"
                pending = found[0] if found is not None else None
        if proposal is not None:
            box, points, confidence, clipped = proposal
            previous_frame, last_seen = gray, at
            patch = _patch(gray, box).copy()
            if manual:
                anchor = patch
            latest = patch
            pending, covered, crossed_cut = None, False, False
            if reason == "confirmed_image_match":
                state = "redetected"
            elif clipped:
                state, reason = "partially_occluded", "tracked_box_intersects_source_edge"
            else:
                state = "partially_occluded" if overlap_fraction(box, overlay) > .03 else "visible"
            if points is None or len(points) < 14:
                points = _points(gray, box, overlay)
            observations.append(Observation(at, state, confidence, _scaled(box, sample_size, source_size), reason))
            continue
        previous_frame = None
        if covered:
            state, reason = "covered_by_source_overlay", "overlay_covers_recent_target_region"
        elif crossed_cut and box is not None:
            state, reason = "unknown", "scene_cut_requires_verified_seed"
        elif box is None:
            state = "not_yet_observed"
            reason = "seed_covered_or_unavailable" if seed is not None else "awaiting_verified_seed"
        else:
            exhausted = attempts >= 12 or at - lost_since > 2250
            state, reason = "tracker_lost", "bounded_search_exhausted" if exhausted else "insufficient_pixel_support"
        observations.append(Observation(at, state, 0., None, reason))
    return observations


def track_source(source: Path, plan: SubjectPlan, spans: tuple[SourceSpan, ...], size: tuple[int, int]) -> dict:
    scale = min(1., 896 / max(size))
    sample_size = tuple(max(2, round(d * scale)) for d in size)
    required = sorted({seed.at_ms for seed in plan.seeds} | {span.start_ms for span in spans})
    frames = _frames(source, spans[0].start_ms, spans[-1].end_ms, sample_size, required)
    observations = follow_subject(frames, plan, spans, size, sample_size)
    if not observations:
        raise ValueError("SubjectLock found no decoded observations inside the clip")
    return {"schema_version": TRACK_SCHEMA, **asdict(plan),
            "tracker_revision": TRACK_REVISION, "sample_step_ms": STEP_MS,
            "source_size": size, "analysis_size": sample_size,
            "source_spans": [asdict(span) for span in spans],
            "observations": [asdict(row) for row in observations]}
