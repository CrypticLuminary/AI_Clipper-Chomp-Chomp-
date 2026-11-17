"""Compare proposed webcam boundaries with the same source coordinates over time."""
from __future__ import annotations

from collections import Counter

from .media import Rectangle
from .subject_lock import overlap_fraction


def face_tracks(samples) -> list[list[tuple[int, Rectangle]]]:
    """Associate detections on the source grid while their positions overlap."""
    tracks: list[list[tuple[int, Rectangle]]] = []
    for index, sample in enumerate(samples):
        pairs = []
        for track_id, track in enumerate(tracks):
            seen, earlier = track[-1]
            if index - seen > 3:
                continue
            for face_id, face in enumerate(sample.faces):
                if face_agreement(earlier, face):
                    pairs.append((track_id, face_id))
        track_matches = Counter(track_id for track_id, _ in pairs)
        face_matches = Counter(face_id for _, face_id in pairs)
        linked = set()
        for track_id, face_id in pairs:
            if track_matches[track_id] == face_matches[face_id] == 1:
                tracks[track_id].append((index, sample.faces[face_id]))
                linked.add(face_id)
        tracks.extend([(index, face)] for face_id, face in enumerate(sample.faces) if face_id not in linked)
    return tracks


def face_agreement(a: Rectangle, b: Rectangle) -> bool:
    return min(overlap_fraction(a, b), overlap_fraction(b, a)) >= .55


def competing_faces(tracks: list[list[tuple[int, Rectangle]]]) -> bool:
    """Ignore brief detections but flag two faces co-visible over several samples."""
    visible = Counter(index for track in tracks if len(track) >= 4 for index, _ in track)
    return any(count >= 2 and all(visible[index + offset] >= 2 for offset in (1, 2, 3))
               for index, count in visible.items())


def perimeter_support(frames, box: Rectangle) -> tuple[float | None, ...]:
    """Fraction of each side backed by a contrast seam in repeated frames.

    Source edges carry no evidence. Evaluate a small range of fixed lines to
    allow for resampling without letting a moving edge vote at a new position.
    """
    import numpy as np

    x, y, w, h = box
    transposed = frames.transpose(0, 2, 1, 3)
    boundaries = ((frames, x, y, h), (frames, x + w, y, h),
                  (transposed, y, x, w), (transposed, y + h, x, w))
    support = []
    for samples, position, first, length in boundaries:
        width = samples.shape[2]
        if min(position, width - position) < 3:
            support.append(None)
            continue
        margin = max(1, length // 12)
        patch = samples[:, first + margin:first + length - margin]
        if patch.shape[1] < 8:
            support.append(0.)
            continue
        best = 0.
        for line in range(max(2, position - 2), min(width - 2, position + 2) + 1):
            before = patch[:, :, line - 2:line].mean(axis=2)
            after = patch[:, :, line:line + 2].mean(axis=2)
            contrast = np.abs(before - after).mean(axis=2)
            sections = np.array_split(contrast >= 12, 8, axis=1)
            repeated = [((section.mean(axis=1) >= .45).mean() >= .65) for section in sections]
            best = max(best, sum(repeated) / len(sections))
        support.append(best)
    return tuple(support)


def enclosing_perimeter(support: tuple[float | None, ...]) -> bool:
    visible = [value for value in support if value is not None]
    return len(visible) >= 2 and min(visible) >= .50 and max(visible) >= .75
