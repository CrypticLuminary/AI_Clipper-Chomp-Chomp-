"""Motion policy for a fixed-size crop within a verified picture region."""
from __future__ import annotations

from math import hypot


def camera_path(initial: tuple[int, int], targets: list[tuple[int, int] | None],
                crop_size: tuple[int, int], limits: tuple[tuple[int, int], tuple[int, int]],
                mode: str) -> list[tuple[int, int]]:
    """Run on evenly spaced observations. Each call starts a fresh shot."""
    if mode == "hold":
        return [initial] * len(targets)

    position = [float(value) for value in initial]
    velocities = [0., 0.]
    moving = mode == "track"
    pending = None
    output = []
    for target in targets:
        observed = target is not None
        if not observed:
            target = tuple(position)
            moving, pending = False, None
        elif mode == "track":
            moving = True
        offsets = tuple((goal - pos) / size for goal, pos, size in zip(target, position, crop_size))
        distance = hypot(*offsets)
        if mode == "pan":
            if moving and distance < .045:
                moving = False
            elif not moving and distance > .13:
                if pending is not None and sum(a * b for a, b in zip(pending, offsets)) > 0:
                    moving = True
                pending = None if moving else offsets
            else:
                pending = None

        for axis, (size, goal, (lower, upper)) in enumerate(zip(crop_size, target, limits)):
            delta = goal - position[axis]
            cap = max(2., size * .07)
            acceleration = cap * .45
            desired = max(-cap, min(cap, delta * .55)) if moving else 0.
            change = max(-acceleration, min(acceleration, desired - velocities[axis]))
            velocity = velocities[axis] + change
            if moving and delta * velocity > 0 and abs(velocity) > abs(delta):
                velocity = delta
            position[axis] = max(lower, min(upper, position[axis] + velocity))
            velocities[axis] = 0. if position[axis] in (lower, upper) else velocity

        output.append((round(position[0]), round(position[1])))
    return output
