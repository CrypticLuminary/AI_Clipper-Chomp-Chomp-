"""Optional evidence for a foreground portrait composited into a recording."""
from __future__ import annotations

from pathlib import Path
from statistics import median

from .camera_evidence import face_tracks
from .media import Rectangle
from .subject_lock import overlap_fraction


class PersonSegmenter:
    """PP-HumanSeg via the existing OpenCV runtime, using a local model."""

    def __init__(self, model: Path):
        import cv2

        self.network = cv2.dnn.readNetFromONNX(str(model))

    def __call__(self, frame, face: Rectangle):
        """Return the connected foreground component anchored by the face."""
        import cv2
        import numpy as np

        height, width = frame.shape[:2]
        x, y, w, h = face
        left, top = max(0, x - round(1.25 * w)), max(0, y - round(.60 * h))
        right, bottom = min(width, x + round(2.25 * w)), min(height, y + round(2.55 * h))
        patch = frame[top:bottom, left:right]
        blob = cv2.dnn.blobFromImage(patch, 1 / 127.5, (192, 192), (127.5,) * 3, swapRB=True)
        self.network.setInput(blob)
        scores = self.network.forward()
        if scores.ndim != 4 or scores.shape[:2] != (1, 2) or not np.isfinite(scores).all():
            raise ValueError("Expected a local, two-class PP-HumanSeg ONNX model")
        background, portrait = scores[0]
        mask = cv2.resize(portrait - background, (right - left, bottom - top)) > .40
        _, labels = cv2.connectedComponents(mask.astype(np.uint8))
        anchor = labels[y - top + h // 4:y - top + 3 * h // 4, x - left + w // 4:x - left + 3 * w // 4]
        label = int(np.bincount(anchor.ravel()).argmax())
        if label == 0 or (anchor == label).mean() < .75:
            return None
        component = np.zeros((height, width), dtype=bool)
        component[top:bottom, left:right] = labels == label
        return component


def _surround_moves(a, b, face: Rectangle, earlier_mask, mask) -> bool:
    """Look for motion in both nearby background strips relative to the face."""
    import numpy as np

    x, y, w, h = face
    height, width = mask.shape
    delta = np.abs(a.astype(np.int16) - b).mean(axis=2)
    ys, xs = slice(y + h // 8, y + 7 * h // 8), slice(x + w // 8, x + 7 * w // 8)
    stable_face = (earlier_mask & mask)[ys, xs]
    if not stable_face.any():
        return False
    head_change = np.median(delta[ys, xs][stable_face])
    outside = ~(earlier_mask | mask)
    ys = slice(max(0, y - h // 3), min(height, y + h))
    for xs in (slice(max(0, x - w), x), slice(x + w, min(width, x + 2 * w))):
        strip = outside[ys, xs]
        if strip.sum() < w * h / 6 or np.median(delta[ys, xs][strip]) < max(12., 1.25 * head_change):
            return False
    return True


def _mask_evidence(track, samples, segment, frame_size: tuple[int, int]) -> dict | None:
    from math import ceil

    import numpy as np

    width, height = frame_size
    # A screen-stationary subject can be a game character. Require a persistent
    # portrait near a source edge and independently moving surroundings as well.
    face = tuple(round(median(values)) for values in zip(*(face for _, face in track)))
    x, y, w, h = face
    if min(x, width - x - w) > 2.0 * w or sum(
        min(overlap_fraction(face, other), overlap_fraction(other, face)) >= .52 for _, other in track
    ) < .72 * len(track):
        return None

    # Bound optional inference for longer clips while retaining adjacent pairs
    # on the 4 Hz source grid.
    stride = max(2, ceil(len(track) / 24))
    selected = [entry for i, entry in enumerate(track) if i % stride < 2]
    mask_count = pairs = continuous = independent_motion = edge_masks = 0
    previous = None
    for index, box in selected:
        frame = samples[index].game
        mask = segment(frame, box)
        if mask is None or not .015 <= mask.mean() <= .28:
            previous = None
            continue
        mask_count += 1
        edge_masks += max(mask[0].sum(), mask[-1].sum(), mask[:, 0].sum(), mask[:, -1].sum()) >= .30 * w
        if previous is not None and index - previous[0] == 1:
            prior_index, prior_mask = previous
            pairs += 1
            if (prior_mask & mask).sum() >= .65 * (prior_mask | mask).sum():
                continuous += 1
                independent_motion += _surround_moves(samples[prior_index].game, frame, box, prior_mask, mask)
        previous = (index, mask)
    if (mask_count < .80 * len(selected) or edge_masks < .58 * mask_count or pairs < 4
            or continuous < .70 * pairs or independent_motion < max(3, .10 * pairs)):
        return None
    return {"model": "PP-HumanSeg", "face_samples": len(track), "mask_samples": mask_count,
            "mask_pairs": pairs, "continuous_mask_pairs": continuous,
            "independent_background_pairs": independent_motion, "edge_mask_samples": int(edge_masks),
            "face_anchor": face, "start_ms": samples[track[0][0]].time_ms,
            "last_observed_ms": samples[track[-1][0]].time_ms}


def classify_cutout(samples, model: Path, frame_size: tuple[int, int]) -> dict | None:
    """Report a cutout only for one well-supported face and mask trajectory."""
    tracks = [track for track in face_tracks(samples) if len(track) >= .75 * len(samples)
              and track[0][0] <= 2 and track[-1][0] >= len(samples) - 3]
    if not tracks:
        return None
    segmenter = PersonSegmenter(model)
    evidence = [result for track in tracks if (result := _mask_evidence(track, samples, segmenter, frame_size))]
    return evidence[0] if len(evidence) == 1 else None
