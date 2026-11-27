"""Resolve a creator's editorial preferences and enforceable campaign constraints."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

from .contract import read_json
from ..clipcontrol import MAX_CLIPS


def milliseconds(value: object) -> int:
    try:
        seconds = Decimal(str(value))
        if seconds.is_finite() and 0 <= seconds <= 1_000_000_000:
            ms = seconds * 1000
            if ms == ms.to_integral_value():
                return int(ms)
    except InvalidOperation:
        pass
    raise ValueError("Use nonnegative seconds with at most three decimal places")


@dataclass(frozen=True)
class Campaign:
    clips: int = 3
    min_duration: int | float = 20
    duration: int | float = 35
    max_duration: int | float = 60
    brief: str = ""
    platform: str = "YouTube Shorts"
    aspect_ratio: str = "9:16"
    preferred_topics: tuple[str, ...] = ()
    excluded_topics: tuple[str, ...] = ()
    captions: bool = True
    rights_ref: str = ""
    human_review: bool = True

    @property
    def durations_ms(self) -> tuple[int, int, int]:
        return tuple(milliseconds(value) for value in (self.min_duration, self.duration, self.max_duration))

    def violation(self, start: int, end: int, text: str) -> str | None:
        minimum, _, maximum = self.durations_ms
        if not minimum <= end - start <= maximum:
            return "outside_campaign_duration"
        if self.captions and not text.strip():
            return "caption_source_missing"
        # Exclusions are also sent to the Director for semantic review.
        normalized = " ".join(re.findall(r"\w+", text.casefold()))
        for topic in self.excluded_topics:
            phrase = " ".join(re.findall(r"\w+", topic.casefold()))
            if f" {phrase} " in f" {normalized} ":
                return "excluded_transcript_phrase"
        return None


def resolve_campaign(path: Path | None = None, **overrides) -> Campaign:
    config = read_json(path) if path else {}
    if not isinstance(config, dict):
        raise ValueError("Campaign must be a JSON object")
    unknown = config.keys() - Campaign.__dataclass_fields__.keys()
    if unknown:
        raise ValueError(f"Unknown campaign fields: {', '.join(sorted(unknown))}")
    config.update({key: value for key, value in overrides.items() if value is not None})
    adjusted_bounds = any(overrides.get(key) is not None for key in ("min_duration", "max_duration"))
    if "duration" not in config or (adjusted_bounds and overrides.get("duration") is None):
        lower = config.get("min_duration", Campaign.min_duration)
        upper = config.get("max_duration", Campaign.max_duration)
        target = config.get("duration", Campaign.duration)
        if all(type(value) in (int, float) for value in (lower, upper, target)):
            milliseconds(target)
            config["duration"] = min(upper, max(lower, target))
    for key in ("preferred_topics", "excluded_topics"):
        if key in config:
            values = config[key]
            if (not isinstance(values, (list, tuple)) or len(values) > 30
                    or any(not isinstance(value, str) or len(value) > 180 or not re.search(r"\w", value)
                           for value in values)):
                raise ValueError(f"{key} requires up to 30 short topic phrases")
            config[key] = tuple(value.strip() for value in values)
    campaign = Campaign(**config)
    if type(campaign.clips) is not int or not 0 <= campaign.clips <= MAX_CLIPS:
        raise ValueError(f"clips must be an integer from 0 to {MAX_CLIPS}")
    for name in ("min_duration", "duration", "max_duration"):
        if type(getattr(campaign, name)) not in (int, float):
            raise ValueError(f"{name} must be numeric seconds")
    minimum, target, maximum = campaign.durations_ms
    if not 0 < minimum <= target <= maximum:
        raise ValueError("Require 0 < min-duration <= duration <= max-duration")
    for name, limit in (("brief", 6000), ("platform", 120), ("rights_ref", 500)):
        value = getattr(campaign, name)
        if not isinstance(value, str) or len(value) > limit or (name != "brief" and not value.strip()):
            detail = " Supply a source rights reference with --rights-ref." if name == "rights_ref" else ""
            raise ValueError(f"Invalid {name}.{detail}")
    if not isinstance(campaign.aspect_ratio, str) or not re.fullmatch(r"[1-9]\d{0,3}:[1-9]\d{0,3}", campaign.aspect_ratio):
        raise ValueError("aspect_ratio must use width:height, for example 9:16")
    if type(campaign.captions) is not bool or campaign.human_review is not True:
        raise ValueError("captions must be a boolean and human_review must be true")
    return campaign
