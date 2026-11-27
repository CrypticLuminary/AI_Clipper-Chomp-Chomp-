"""Shared preferences and enforceable limits for completed and live clipping."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import re

MAX_CLIPS = 20
RESOLUTIONS = (540, 720, 1080)
ASPECTS = ("9:16", "16:9", "1:1")
LAYOUTS = ("shorts", "fit", "letterbox", "facecam-gameplay", "gameplay-only")
PRESETS = {
    "shorts": {"aspect": "9:16", "resolution": 540, "min_ms": 6000, "max_ms": 45000, "preferred_ms": 28000},
    "story": {"aspect": "9:16", "resolution": 1080, "min_ms": 15000, "max_ms": 60000, "preferred_ms": 40000},
    "landscape": {"aspect": "16:9", "resolution": 720, "min_ms": 6000, "max_ms": 45000,
                  "preferred_ms": 25000, "layout": "fit"},
}


@dataclass(frozen=True)
class ClipControl:
    clips: int = 3
    mode: str = "UP_TO"
    min_ms: int = 6000
    max_ms: int = 45000
    preferred_ms: int | None = None
    campaign_min_ms: int = 1000
    campaign_max_ms: int = 60000
    aspect: str = "9:16"
    resolution: int = 540
    container: str = "mp4"
    codec: str = "h264"
    captions: bool = True
    caption_position: str = "lower"
    audio_profile: str = "original"
    audio_stream: int = 0
    layout: str = "shorts"
    facecam_roi: tuple[int, int, int, int] | None = None
    gameplay_roi: tuple[int, int, int, int] | None = None
    hardware: str = "eco"
    instructions: str = ""
    preferred_topics: tuple[str, ...] = ()
    excluded_topics: tuple[str, ...] = ()
    source_start_ms: int | None = None
    source_end_ms: int | None = None
    proposals_per_hour: int = 40
    reasoning_calls: int | None = None
    reasoning_seconds: int = 600
    engine: str = "evolution"

    def __post_init__(self):
        for name, lo, hi in (
            ("clips", 0, MAX_CLIPS), ("min_ms", 1000, 60000), ("max_ms", 1000, 60000),
            ("campaign_min_ms", 1000, 60000), ("campaign_max_ms", 1000, 60000),
            ("audio_stream", 0, 63), ("proposals_per_hour", 1, 240), ("reasoning_seconds", 10, 3600),
        ):
            val = getattr(self, name)
            if type(val) is not int or not lo <= val <= hi:
                raise ValueError(f"{name} must be an integer from {lo} to {hi}")
        lo, hi = self.bounds
        if self.min_ms > self.max_ms or self.campaign_min_ms > self.campaign_max_ms or lo > hi:
            raise ValueError("Campaign and requested duration ranges must intersect")
        if self.preferred_ms is not None and (type(self.preferred_ms) is not int or
                                              not self.min_ms <= self.preferred_ms <= self.max_ms):
            raise ValueError("Preferred duration must lie within the requested duration range")
        if self.mode not in {"TARGET", "UP_TO"} or self.aspect not in ASPECTS or self.resolution not in RESOLUTIONS:
            raise ValueError("Choose a supported count mode, aspect ratio, and resolution")
        if self.container != "mp4" or self.codec != "h264":
            raise ValueError("The supported output is MP4 with H.264 video and AAC audio")
        if self.layout not in LAYOUTS or self.hardware not in {"eco", "balanced", "gpu"}:
            raise ValueError("Choose a supported layout and hardware profile")
        if self.engine not in {"evolution", "heuristic"} or self.caption_position not in {"upper", "middle", "lower"}:
            raise ValueError("Choose a supported engine and caption position")
        if type(self.captions) is not bool or self.audio_profile not in {"original", "balanced"}:
            raise ValueError("Invalid caption or audio settings")
        if not isinstance(self.instructions, str) or len(self.instructions) > 4000:
            raise ValueError("Instructions must be text up to 4000 characters")
        for topics in (self.preferred_topics, self.excluded_topics):
            if not isinstance(topics, (tuple, list)) or len(topics) > 20 or any(
                    not isinstance(t, str) or not 0 < len(t) <= 150 or not t.strip() for t in topics):
                raise ValueError("Supply at most 20 nonempty topics with up to 150 characters each")
        for rect in (self.facecam_roi, self.gameplay_roi):
            if rect is not None and (not isinstance(rect, (list, tuple)) or len(rect) != 4 or
                                     any(type(x) is not int or x < 0 or x > 16384 for x in rect) or not all(rect[2:])):
                raise ValueError("Regions require integer x, y, width and height in source pixels")
        if self.layout == "facecam-gameplay" and not self.facecam_roi:
            raise ValueError("A manual streamer region is required for a fixed split layout")
        for val in (self.source_start_ms, self.source_end_ms):
            if val is not None and (type(val) is not int or not 0 <= val <= 10**10):
                raise ValueError("Source range must use nonnegative integer milliseconds")
        if self.source_end_ms is not None and self.source_end_ms - (self.source_start_ms or 0) < lo:
            raise ValueError("Requested source range is shorter than the effective minimum")
        if self.reasoning_calls is not None and (type(self.reasoning_calls) is not int or not 2 <= self.reasoning_calls <= 120):
            raise ValueError("Reasoning calls must be an integer from 2 to 120")

    @property
    def bounds(self):
        return max(self.min_ms, self.campaign_min_ms), min(self.max_ms, self.campaign_max_ms)

    @property
    def preferred(self):
        low, high = self.bounds
        target = self.preferred_ms if self.preferred_ms is not None else (low + high) // 2
        return min(high, max(low, target))

    @property
    def dimensions(self):
        w, h = (int(s) for s in self.aspect.split(":"))
        def even(x):
            return round(x / 2) * 2
        return even(self.resolution * w / min(w, h)), even(self.resolution * h / min(w, h))

    @property
    def calls(self):
        return self.reasoning_calls if self.reasoning_calls is not None else min(120, max(8, self.clips * 4 + 4))

    def source_range(self, duration_ms):
        start = self.source_start_ms or 0
        end = self.source_end_ms if self.source_end_ms is not None else duration_ms
        if end > duration_ms or end - start < self.bounds[0]:
            raise ValueError("The recording cannot satisfy this source range and minimum duration")
        return start, end

    def check_span(self, start, end, duration):
        left, right = self.source_range(duration)
        low, high = self.bounds
        if not left <= start < end <= right or not low <= end - start <= high:
            raise ValueError("Clip is outside the effective source range or duration limits")

    def permits_text(self, text):
        normalized = " " + " ".join(re.findall(r"\w+", text.casefold())) + " "
        return not any(" " + " ".join(tokens) + " " in normalized for topic in self.excluded_topics
                       if (tokens := re.findall(r"\w+", topic.casefold())))

    def public(self):
        return {**asdict(self), "effective_min_ms": self.bounds[0], "effective_max_ms": self.bounds[1],
                "effective_preferred_ms": self.preferred, "output_width": self.dimensions[0],
                "output_height": self.dimensions[1], "effective_reasoning_calls": self.calls}

    @classmethod
    def parse(cls, values):
        if not isinstance(values, dict):
            raise ValueError("Clip requirements must be a JSON object")
        unknown = values.keys() - cls.__dataclass_fields__.keys()
        if unknown:
            raise ValueError("Unknown clip requirements: " + ", ".join(sorted(unknown)))
        return cls(**values)

    def export_summary(self, produced: int, decisions=()):
        from collections import Counter
        return {"mode": self.mode, "requested": self.clips, "produced": produced,
                "shortfall": max(0, self.clips - produced) if self.mode == "TARGET" else None,
                "explanation": ("The available distinct candidates did not meet the configured editorial, duration, or processing requirements."
                                if self.mode == "TARGET" and produced < self.clips else None),
                "decisions": dict(Counter(decisions))}