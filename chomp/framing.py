"""Optional facecam framing for selected local VOD ranges."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import file_digest
from math import ceil, hypot
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING

from .media import resolve_layout

if TYPE_CHECKING:
    from numpy.typing import NDArray

Rectangle = tuple[int, int, int, int]


@dataclass(frozen=True)
class FramingPlan:
    layout: str
    facecam_roi: Rectangle | None
    detection: dict[str, str | int] | None = None

    @property
    def fit_facecam(self) -> bool:
        return self.detection is not None and self.detection.get("method") == "recurring_face"


def facecam_fingerprint(model: Path | None) -> dict[str, str | None]:
    """Separate automatic review artifacts when the local detector changes."""
    try:
        import cv2
    except ImportError:
        version = None
    else:
        version = cv2.__version__

    model_hash = None
    if model is not None:
        if not model.is_file():
            raise ValueError(f"YuNet model file does not exist: {model}")
        with model.open("rb") as handle:
            model_hash = file_digest(handle, "sha256").hexdigest()
    return {"opencv": version, "yunet_sha256": model_hash}


def _center(box: Rectangle) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2, y + h / 2


def _pick_facecam(
    detections: list[list[Rectangle]], gray: list[NDArray], width: int, height: int,
) -> tuple[Rectangle | None, int, str]:
    """Require a small edge face that recurs through the clip and changes across frames."""
    import numpy as np

    samples = len(detections)
    if samples < 3:
        return None, 0, "fewer than three decoded samples"
    required = max(3, ceil(.6 * samples))

    def is_small_edge_face(box: Rectangle) -> bool:
        cx, cy = _center(box)
        return (0.02 * width <= box[2] <= .14 * width and .035 * height <= box[3] <= .26 * height
                and (cx < .30 * width or cx > .70 * width or cy < .22 * height or cy > .78 * height))

    faces = [sorted(filter(is_small_edge_face, sample), key=lambda box: box[2] * box[3], reverse=True)[:12]
             for sample in detections]

    def distance(a: Rectangle, b: Rectangle) -> float:
        ax, ay = _center(a)
        bx, by = _center(b)
        return hypot((ax - bx) / (.052 * width), (ay - by) / (.09 * height))

    tracks = set()
    for seed in {box for sample in faces for box in sample}:
        matched = []
        for index, sample in enumerate(faces):
            nearest = min(sample, key=lambda box: distance(seed, box), default=None)
            if nearest is not None and distance(seed, nearest) <= 1:
                matched.append((index, nearest))
        if len(matched) >= required and matched[0][0] < ceil(samples / 3) and matched[-1][0] >= samples * 2 // 3:
            tracks.add(tuple(matched))

    accepted: list[tuple[Rectangle, set[int]]] = []
    for track in sorted(tracks, key=lambda track: (-len(track), track)):
        boxes = [box for _, box in track]
        box = tuple(round(median(values)) for values in zip(*boxes))
        if any(distance(box, previous) <= 1 for previous, _ in accepted):
            continue
        x, y, w, h = box
        patches = [frame[max(0, y):y + h, max(0, x):x + w] for frame in gray]
        if not patches[0].size or float(np.std(patches, axis=0).mean()) < 2.5:
            continue
        accepted.append((box, {index for index, _ in track}))

    if not accepted:
        return None, 0, "no recurring edge face with enough motion"
    best, matched = accepted[0]
    support = len(matched)
    if len(accepted) > 1 and len(accepted[1][1]) >= support - 1:
        return None, support, "multiple similarly supported faces"
    # A webcam can be enlarged or moved temporarily while its small position is
    # still the most frequent. Avoid cropping out the enlarged face at that point.
    displaced = [
        index not in matched and any(
            face[2] >= .85 * best[2] and face[3] >= .85 * best[3] and distance(best, face) > 1
            for face in sample
        )
        for index, sample in enumerate(detections)
    ]
    if any(a and b for a, b in zip(displaced, displaced[1:])):
        return None, support, "face position or scale changes within the clip"
    return best, support, "small face recurs throughout the selected range"


def _padded_roi(box: Rectangle, width: int, height: int) -> Rectangle:
    """Leave room around the face and clamp to even source pixel coordinates."""
    cx, cy = _center(box)
    face_width, face_height = box[2:]
    w = max(2, round(min(width * .43, face_width * 2.7) / 2) * 2)
    h = max(2, round(min(height * .49, face_height * 2.35) / 2) * 2)
    left = max(0, min(width - w, round(cx - w / 2))) // 2 * 2
    top = max(0, min(height - h, round(cy + .14 * face_height - h / 2))) // 2 * 2
    return left, top, w, h


def _sample_facecam(source: Path, start_ms: int, end_ms: int, model: Path | None) -> FramingPlan:
    def fallback(detector: str, reason: str, samples: int = 0, support: int = 0) -> FramingPlan:
        return FramingPlan("fit", None, {"method": "full_frame", "detector": detector, "samples": samples,
                                         "matching_frames": support, "reason": reason})

    try:
        import cv2
    except ImportError:
        return fallback("unavailable", "install chomp-chomp[facecam] to enable detection")

    detector_name = "yunet" if model is not None else "haar"
    cap = cv2.VideoCapture(str(source))
    try:
        if not cap.isOpened():
            return fallback(detector_name, "OpenCV could not open the video")
        # Frame orientation must agree with the renderer's source pixel coordinates.
        if cap.get(cv2.CAP_PROP_ORIENTATION_META) % 360:
            return fallback(detector_name, "source has rotation metadata")

        if model is not None:
            try:
                detector = cv2.FaceDetectorYN.create(str(model), "", (320, 320), .65, .3, 200)
            except cv2.error as exc:
                raise ValueError(f"Could not load the supplied YuNet model: {model}") from exc
        else:
            detector = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
            if detector.empty():
                return fallback(detector_name, "OpenCV's bundled face detector could not be loaded")

        sample_count = min(12, max(3, (end_ms - start_ms) // 700))
        frames: list[NDArray] = []
        boxes: list[list[Rectangle]] = []
        source_size = None
        for index in range(sample_count):
            at_ms = start_ms + (end_ms - start_ms) * (index + .5) / sample_count
            if not cap.set(cv2.CAP_PROP_POS_MSEC, at_ms):
                continue
            ok, frame = cap.read()
            if not ok:
                continue
            source_height, source_width = frame.shape[:2]
            if source_width < 1.2 * source_height:
                return fallback(detector_name, "automatic facecam framing expects a landscape source")
            if source_size is not None and source_size != (source_width, source_height):
                return fallback(detector_name, "video dimensions changed between samples", len(frames))
            source_size = (source_width, source_height)
            width = min(960, source_width)
            height = round(source_height * width / source_width)
            frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if model is not None:
                detector.setInputSize((width, height))
                try:
                    _, found = detector.detect(frame)
                except cv2.error as exc:
                    raise ValueError(f"The supplied model is incompatible with YuNet: {model}") from exc
                detections = [] if found is None else found[:, :4]
            else:
                detections = detector.detectMultiScale(cv2.equalizeHist(gray), 1.1, 6, minSize=(18, 18))
            frames.append(gray)
            boxes.append([tuple(round(float(value)) for value in row) for row in detections])
    finally:
        cap.release()

    if len(frames) < max(3, ceil(sample_count * .75)):
        return fallback(detector_name, "insufficient samples across the selected range", len(frames))
    best, support, reason = _pick_facecam(boxes, frames, width, height)
    if best is None:
        return fallback(detector_name, reason, len(frames), support)
    x, y, w, h = best
    source_box = (round(x * source_width / width), round(y * source_height / height),
                  round(w * source_width / width), round(h * source_height / height))
    return FramingPlan("facecam-gameplay", _padded_roi(source_box, source_width, source_height),
                       {"method": "recurring_face", "detector": detector_name, "samples": len(frames),
                        "matching_frames": support, "reason": reason})


def plan_framing(
    source: Path, start_ms: int, end_ms: int, layout: str,
    facecam_roi: Rectangle | None, facecam_model: Path | None = None,
) -> FramingPlan:
    if layout != "auto-facecam":
        return FramingPlan(resolve_layout(layout, facecam_roi), facecam_roi)
    if facecam_roi is not None:
        return FramingPlan("facecam-gameplay", facecam_roi, {"method": "supplied_roi"})
    return _sample_facecam(source, start_ms, end_ms, facecam_model)
