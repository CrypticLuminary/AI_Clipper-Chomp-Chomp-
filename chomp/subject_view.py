"""Select a viewport from observed subject geometry and protected source regions."""
from __future__ import annotations

from bisect import bisect_right

from .camera_motion import camera_path
from .media import Rectangle, crop_position
from .subject_lock import STEP_MS, _scaled, overlap_fraction


def _cover(domain, size, preferred, boxes):
    x, y, w, h = domain
    rw, rh = size
    positions = []
    for axis, (origin, span, length) in enumerate(((x, w, rw), (y, h, rh))):
        lower, upper = origin, origin + span - length
        edge = max(lower, max((r[axis] + r[axis + 2] - length for r in boxes), default=lower))
        other = min(upper, min((r[axis] for r in boxes), default=upper))
        if edge > other:
            edge, other = lower, upper
        positions.append(max(edge, min(other, preferred[axis])))
    return *positions, rw, rh


def observation_at(observations, times: list[int], at: int):
    index = bisect_right(times, at) - 1
    return observations[index] if index >= 0 and at - times[index] <= STEP_MS * 2 else None


def subject_windows(track: dict, times: list[int], source_size: tuple[int, int], game_size: tuple[int, int],
                    picture: Rectangle, visible: list[Rectangle], aspect: float, mode: str):
    """Leave the crop still during missing observations and keep the same destination layout."""
    if not visible:
        return None, [], 0.
    observations = track["observations"]
    clocks = [row["at_ms"] for row in observations]
    scene = []
    for time in times:
        row = observation_at(observations, clocks, time)
        box = _scaled(row["box"], source_size, game_size) if row and row["box"] and row["confidence"] >= .73 else None
        protected = [_scaled(region["box"], source_size, game_size) for region in track["protected_regions"]
                     if region["start_ms"] <= time < region["end_ms"]]
        scene.append((box, protected))

    def view(domain, size, target, protected, previous):
        x, y, w, h = domain
        rw, rh = size
        if target is None and not protected:
            return _cover(domain, size, previous, ())
        focus = target or protected[0]
        padding = max(2, round(rw * .055)), max(2, round(rh * .055))
        px, py = padding
        inner = (previous[0] + px, previous[1] + py, rw - 2 * px, rh - 2 * py)
        current = (*previous, rw, rh)
        if ((target is None or overlap_fraction(target, inner) >= .94)
                and all(overlap_fraction(region, current) >= .99 for region in protected)):
            return current
        center = round(focus[0] + (focus[2] - rw) / 2), round(focus[1] + (focus[3] - rh) / 2)
        candidates = (_cover(domain, size, center, [focus, *protected]), _cover(domain, size, center, [focus]),
                      _cover(domain, size, (round(x + (w - rw) / 2), round(y + (h - rh) / 2)), protected),
                      _cover(domain, size, previous, [focus, *protected]))
        return max(candidates, key=lambda candidate: score(candidate, target, protected, padding))

    def score(crop, target, protected, padding):
        x, y, w, h = crop
        px, py = padding
        inner = x + px, y + py, w - 2 * px, h - 2 * py
        target_score = 3 * overlap_fraction(target, crop) + overlap_fraction(target, inner) if target else 0.
        return target_score + sum(overlap_fraction(region, crop) for region in protected) / max(1, len(protected))

    def plan(domain):
        _, _, w, h = domain
        size = min(w, max(2, round(h * aspect))), min(h, max(2, round(w / aspect)))
        rw, rh = size
        px, py, pw, ph = picture
        center = round(px + (pw - rw) / 2), round(py + (ph - rh) / 2)
        # A scene starts with its first grounded view, or a centered context view.
        first = view(domain, size, *scene[0], _cover(domain, size, center, ())[:2])
        desired = first[:2]
        targets = []
        strength = 0.
        for target, protected in scene:
            if target is None and not protected:
                targets.append(None)
                continue
            selected = view(domain, size, target, protected, desired)
            strength += score(selected, target, protected, (0, 0))
            desired = selected[:2]
            targets.append(desired)
        area = rw * rh / (picture[2] * picture[3])
        return strength / len(scene) + .35 * area, first, size, targets, area

    domain, (_, first, (rw, rh), targets, area) = max(((region, plan(region)) for region in visible),
                                                       key=lambda item: item[1][0])
    x, y, w, h = domain
    positions = camera_path(first[:2], targets, (rw, rh), ((x, x + w - rw), (y, y + h - rh)), mode)
    return first, positions, area


def crop_at(segments, at: int, size: tuple[int, int]) -> Rectangle | None:
    segment = next((item for item in segments if item.start_ms <= at < item.end_ms), None)
    if segment is None:
        return None
    if segment.layout in {"letterbox", "fit"}:
        return 0, 0, *size
    crop = segment.gameplay_roi
    if crop and segment.gameplay_path:
        return *crop_position(segment.gameplay_path, at), *crop[2:]
    return crop


def subject_review(track: dict, segments) -> tuple[str, ...]:
    warnings = set()
    observations = track["observations"]
    unavailable = any(row["box"] is None for row in observations)
    if unavailable:
        warnings.add("Review SubjectLock intervals without reliable target pixels.")
    if any(span["usable_fraction"] < .32 and any(
        span["start_ms"] <= row["at_ms"] < span["end_ms"] and row["box"] is None for row in observations
    ) for span in track["source_spans"]):
        track["recommended_review"] = "consider_streamer_focus"
        warnings.add("A source webcam covers much of the gameplay while the target is unavailable.")
    for row in observations:
        time = row["at_ms"]
        crop = crop_at(segments, time, track["source_size"])
        observed_coverage = round(overlap_fraction(row["box"], crop), 3) if row["box"] else None
        row["planned_target_fraction"] = observed_coverage
        row["planned_protected_fractions"] = {
            item["id"]: round(overlap_fraction(item["box"], crop), 3) for item in track["protected_regions"]
            if item["start_ms"] <= time < item["end_ms"]
        }
        if observed_coverage is not None and observed_coverage < .90:
            warnings.add("Review the planned crop where it excludes part of an observed subject.")
        if any(value < .90 for value in row["planned_protected_fractions"].values()):
            warnings.add("Review the planned crop where a requested protected region falls outside the viewport.")
    return tuple(sorted(warnings))
