"""Compare proposed webcam boundaries with the same source coordinates over time."""
from __future__ import annotations

from .media import Rectangle


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
