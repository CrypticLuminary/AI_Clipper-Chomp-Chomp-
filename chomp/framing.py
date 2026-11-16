"""Optional facecam framing for selected local VOD ranges."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import file_digest
from math import ceil, hypot
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING

from .media import LayoutSegment, resolve_layout

if TYPE_CHECKING:
    from numpy.typing import NDArray

Rectangle = tuple[int, int, int, int]


@dataclass(frozen=True)
class FramingPlan:
    layout: str
    facecam_roi: Rectangle | None
    detection: dict | None = None
    segments: tuple[LayoutSegment, ...] = ()
    warnings: tuple[str, ...] = ()
    subject_tracking: dict | None = None

    @property
    def fit_facecam(self) -> bool:
        return self.facecam_roi is not None


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
            face[2] >= .85 * best[2] and face[3] >= .85 * best[3]
            and (distance(best, face) > 1 or face[2] > 1.45 * best[2] or face[3] > 1.45 * best[3])
            for face in sample
        )
        for index, sample in enumerate(detections)
    ]
    if any(displaced):
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


def _camera_container(face: Rectangle, frames, width: int, height: int) -> Rectangle | None:
    """Look for persistent rectangular seams around a face at the source edge."""
    import numpy as np

    x, y, w, h = face
    cx, cy = _center(face)
    tile = np.asarray(frames[::max(1, len(frames) // 10)], dtype=np.float32)
    a, b = max(0, y - h // 4), min(height, y + h + h // 4)
    c, d = max(0, x - w // 4), min(width, x + w + w // 4)
    if b <= a or d <= c:
        return None
    vertical = np.median(np.mean(np.abs(np.diff(tile[:, a:b, :], axis=2)), axis=1), axis=0)
    horizontal = np.median(np.mean(np.abs(np.diff(tile[:, :, c:d], axis=1)), axis=2), axis=0)

    def seam(strength, lo, hi, expected):
        lo, hi = max(1, round(lo)), min(len(strength), round(hi))
        options = range(lo, hi + 1)
        best = max(options, key=lambda i: strength[i - 1] - abs(i - expected) * .012, default=None)
        return best if best is not None and strength[best - 1] >= 12 else None

    # Requiring all four sides (or a true frame edge) avoids stretching a
    # loosely padded guess across text, chat, or adjacent gameplay.
    left = 0 if cx < 1.75 * w else seam(vertical, x - 3.0 * w, x - .15 * w, cx - 2.0 * w)
    right = width if cx > width - 1.75 * w else seam(vertical, x + 1.15 * w, x + 4.0 * w, cx + 2.0 * w)
    top = 0 if cy < 1.25 * h else seam(horizontal, y - 1.75 * h, y - .1 * h, cy - 1.35 * h)
    bottom = height if cy > height - 1.25 * h else seam(horizontal, y + 1.1 * h, y + 2.75 * h, cy + 1.35 * h)
    if None in (left, right, top, bottom):
        return None
    rw, rh = right - left, bottom - top
    if (not 2 * w <= rw <= min(6 * w, width * .46) or not 1.35 * h <= rh <= height * .48
            or not 1.1 <= rw / rh <= 2.6):
        return None
    return left, top, rw, rh


def _compose_sampled(start_ms: int, end_ms: int, boxes, frames, source_size, detector_name: str) -> FramingPlan:
    """Use stable observed states and full-frame fallback around change or ambiguity."""
    height, width = frames[0].shape
    sw, sh = source_size
    sample_ms = (end_ms - start_ms) / len(frames)

    def small_face(sample):
        small = []
        for face in sample:
            cx, cy = _center(face)
            x, y, w, h = face
            if (.02 * width <= w <= .14 * width and .035 * height <= h <= .26 * height
                    and (cx < .30 * width or cx > .70 * width or cy < .22 * height or cy > .78 * height)):
                small.append(face)
        if len(small) != 1:
            return None
        face = small[0]
        if any(other != face and other[2] > 1.45 * face[2] for other in sample):
            return None
        return face

    def matches(a, b):
        return (a is not None and b is not None and
                abs(_center(a)[0] - _center(b)[0]) <= width * .043 and
                abs(_center(a)[1] - _center(b)[1]) <= height * .065 and
                max(a[2], b[2]) <= 1.38 * min(a[2], b[2]) and
                max(a[3], b[3]) <= 1.38 * min(a[3], b[3]))

    def make_roi(face, images):
        region = _camera_container(face, images, width, height)
        container = region is not None
        region = region or face
        x, y, w, h = region
        mapped = (round(x * sw / width), round(y * sh / height),
                  round((x + w) * sw / width) - round(x * sw / width),
                  round((y + h) * sh / height) - round(y * sh / height))
        return (mapped if container else _padded_roi(mapped, sw, sh)), container

    states = [small_face(sample) for sample in boxes]
    segments = []
    warnings = {"Review the intended streamer camera and protected gameplay at playback speed"}
    support = 0
    i = 0
    while i < len(states):
        j = i + 1
        while j < len(states) and (matches(states[i], states[j]) or states[i] is states[j] is None):
            j += 1
        roi = None
        # Require at least 1.2 seconds of corroboration before re-entering a split.
        if states[i] is not None and j - i >= max(3, ceil(1200 / sample_ms)):
            face, count, _ = _pick_facecam(boxes[i:j], frames[i:j], width, height)
            if face is not None and count == j - i:
                roi, container = make_roi(face, frames[i:j])
                support += count
                if not container:
                    warnings.add("Detector estimated a region around the face. Check webcam edges and adjacent overlays.")
        a = start_ms + round(i * sample_ms)
        b = start_ms + round(j * sample_ms)
        if roi is not None:
            # Leave a short margin of full-frame video at uncertain transitions.
            guard = round(sample_ms / 2)
            if i:
                segments.append(LayoutSegment(a, a + guard, "fit"))
                a += guard
            if j < len(states):
                segments.append(LayoutSegment(a, b - guard, "facecam-gameplay", roi))
                segments.append(LayoutSegment(b - guard, b, "fit"))
            else:
                segments.append(LayoutSegment(a, b, "facecam-gameplay", roi))
        else:
            segments.append(LayoutSegment(a, b, "fit"))
        i = j

    compact: list[LayoutSegment] = []
    for segment in segments:
        if compact and compact[-1].layout == segment.layout == "fit":
            compact[-1] = LayoutSegment(compact[-1].start_ms, segment.end_ms, "fit")
        else:
            compact.append(segment)
    if not compact or not support or len(compact) > 16:
        return FramingPlan("fit", None, {"method": "full_frame", "detector": detector_name,
                           "samples": len(frames), "matching_frames": support,
                           "reason": "No reliable fixed small camera, or rapid layout changes"},
                           warnings=("Full-frame backup preserves context. Check portrait readability.",))
    if len(compact) == 1:
        return FramingPlan("facecam-gameplay", compact[0].facecam_roi,
                           {"method": "recurring_face", "detector": detector_name, "samples": len(frames),
                            "matching_frames": support, "reason": "Stable small webcam across sampled frames"},
                           warnings=tuple(sorted(warnings)))
    return FramingPlan("temporal", None, {"method": "sampled_layout_changes", "detector": detector_name,
                       "samples": len(frames), "matching_frames": support,
                       "reason": "Split stable camera spans and preserve the full frame through uncertain spans"},
                       tuple(compact), tuple(sorted(warnings)))


def _sample_facecam(source: Path, start_ms: int, end_ms: int, model: Path | None) -> FramingPlan:
    detector_name = "yunet" if model is not None else "haar"

    def fallback(reason: str, samples: int = 0) -> FramingPlan:
        return FramingPlan("fit", None, {"method": "full_frame", "detector": detector_name, "samples": samples,
                           "matching_frames": 0, "reason": reason},
                           warnings=("Full-frame backup preserves context. Check portrait readability.",))

    try:
        import cv2
    except ImportError:
        detector_name = "unavailable"
        return fallback("Install chomp-chomp[facecam] to enable detection")

    cap = cv2.VideoCapture(str(source))
    try:
        if not cap.isOpened():
            return fallback("OpenCV could not open the video")
        if cap.get(cv2.CAP_PROP_ORIENTATION_META) % 360:
            return fallback("Source has rotation metadata")
        if model is not None:
            try:
                detector = cv2.FaceDetectorYN.create(str(model), "", (320, 320), .65, .3, 200)
            except cv2.error as exc:
                raise ValueError(f"Could not load the supplied YuNet model: {model}") from exc
        else:
            detector = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
            if detector.empty():
                return fallback("OpenCV's bundled face detector could not be loaded")

        if end_ms - start_ms > 90_000:
            return fallback("Automatic framing is limited to selected ranges of at most 90 seconds")
        count = min(240, max(3, ceil((end_ms - start_ms) / 375)))
        frames: list[NDArray] = []
        boxes: list[list[Rectangle]] = []
        source_size = None
        for i in range(count):
            at_ms = start_ms + (end_ms - start_ms) * (i + .5) / count
            if not cap.set(cv2.CAP_PROP_POS_MSEC, at_ms):
                return fallback("Cannot seek to a required camera sample", len(frames))
            ok, frame = cap.read()
            if not ok:
                return fallback("Cannot decode a required camera sample", len(frames))
            h, w = frame.shape[:2]
            if w < 1.2 * h:
                return fallback("Automatic facecam expects a landscape source")
            if source_size is not None and source_size != (w, h):
                return fallback("Source dimensions changed during the range", len(frames))
            source_size = (w, h)
            width = min(960, w)
            height = round(h * width / w)
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
    return _compose_sampled(start_ms, end_ms, boxes, frames, source_size, detector_name)


def plan_framing(
    source: Path, start_ms: int, end_ms: int, layout: str,
    facecam_roi: Rectangle | None, facecam_model: Path | None = None,
    *, saliency_model: Path | None = None, camera_slot: str = "top",
) -> FramingPlan:
    if layout == "shorts":
        from .reframe import CAMERA_FRACTION, plan_shorts

        if facecam_roi is None:
            return plan_shorts(source, start_ms, end_ms, face_model=facecam_model,
                               saliency_model=saliency_model, camera_slot=camera_slot)
        segments = (LayoutSegment(start_ms, end_ms, "facecam-gameplay", facecam_roi,
                                  preserve_gameplay=False, camera_fraction=CAMERA_FRACTION,
                                  camera_slot=camera_slot),)
        return FramingPlan("facecam-gameplay", facecam_roi, {"method": "supplied_shorts_roi"},
                           segments, ("Confirm the supplied camera panel and gameplay crop.",))
    if layout == "auto-facecam" and facecam_roi is None:
        return _sample_facecam(source, start_ms, end_ms, facecam_model)
    if layout in {"auto", "auto-facecam"}:
        layout = "facecam-gameplay" if facecam_roi is not None else "fit"
    chosen = resolve_layout(layout, facecam_roi)
    if chosen == "facecam-gameplay":
        return FramingPlan(chosen, facecam_roi, {"method": "supplied_roi"},
                           warnings=("Confirm the supplied webcam rectangle throughout the clip, especially after scene changes.",))
    if chosen == "gameplay-only":
        return FramingPlan(chosen, None, warnings=("Check whether the crop removes action or the HUD.",))
    if chosen == "fit":
        return FramingPlan(chosen, None, warnings=("Check that important details are readable in the full-frame portrait view.",))
    return FramingPlan(chosen, None)
