"""Shared source-clock contracts. All times are integer milliseconds."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal
import math

Modality = Literal["audio", "transcript", "chat", "audit"]


def checked_span(start_ms: int, end_ms: int, duration_ms: int) -> None:
    if not all(type(x) is int for x in (start_ms, end_ms, duration_ms)):
        raise TypeError("timestamps must be integer milliseconds")
    if not 0 <= start_ms < end_ms <= duration_ms:
        raise ValueError(f"invalid source-relative span: {start_ms}:{end_ms} / {duration_ms}")


@dataclass(frozen=True)
class TimedWord:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None

    def validate(self, duration_ms: int) -> None:
        checked_span(self.start_ms, self.end_ms, duration_ms)
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Timed words require nonempty text")
        if self.confidence is not None and (
            type(self.confidence) not in (int, float) or not 0 <= self.confidence <= 1
        ):
            raise ValueError("Word confidence must be between 0 and 1")


@dataclass(frozen=True)
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str
    speaker_id: str | None = None
    words: tuple[TimedWord, ...] = ()

    def validate(self, duration_ms: int) -> None:
        checked_span(self.start_ms, self.end_ms, duration_ms)
        if not isinstance(self.text, str):
            raise TypeError("transcript text must be a string")
        if self.speaker_id is not None and (not isinstance(self.speaker_id, str) or not self.speaker_id.strip() or len(self.speaker_id)>96):
            raise ValueError("speaker_id must be a nonempty identifier <=96 chars")
        previous_start = self.start_ms
        for word in self.words:
            word.validate(duration_ms)
            if word.start_ms < previous_start or word.end_ms > self.end_ms:
                raise ValueError("Timed words must be ordered inside their transcript segment")
            previous_start = word.start_ms
        if self.words and "".join("".join(w.text.split()) for w in self.words) != "".join(self.text.split()):
            raise ValueError("Timed words must preserve the transcript segment's text")


@dataclass(frozen=True)
class SpeakerTurn:
    start_ms: int
    end_ms: int
    speaker_id: str

    def validate(self, duration_ms: int) -> None:
        checked_span(self.start_ms, self.end_ms, duration_ms)
        if not isinstance(self.speaker_id, str) or not self.speaker_id.strip() or len(self.speaker_id) > 96:
            raise ValueError("speaker_id must be a nonempty identifier <=96 chars")


@dataclass(frozen=True)
class ChatMessage:
    time_ms: int
    user_id: str
    text: str

    def validate(self, duration_ms: int) -> None:
        if type(self.time_ms) is not int or not 0 <= self.time_ms <= duration_ms:
            raise ValueError("chat time outside source")
        if not isinstance(self.user_id, str) or not isinstance(self.text, str):
            raise ValueError("Chat user ID and text must be strings")


@dataclass(frozen=True)
class Observation:
    modality: Modality
    kind: str
    start_ms: int
    end_ms: int
    strength: float
    evidence: str

    def validate(self, duration_ms: int) -> None:
        checked_span(self.start_ms, self.end_ms, duration_ms)
        if self.modality not in ("audio", "transcript", "chat", "audit"):
            raise ValueError("unsupported modality")
        if not math.isfinite(self.strength) or not 0 <= self.strength <= 1:
            raise ValueError("strength outside 0..1")


@dataclass
class Candidate:
    start_ms: int
    end_ms: int
    anchor_ms: int
    observations: list[Observation] = field(default_factory=list)
    transcript_excerpt: str = ""
    speaker_context: list[dict] = field(default_factory=list)
    categories: list[str] = field(default_factory=lambda: ["other"])
    scores: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    ranking_method: str = "unranked"
    rationale: str = ""
    missing_context: bool = True
    requires_visual_verification: bool = True

    def validate(self, duration_ms: int) -> None:
        checked_span(self.start_ms, self.end_ms, duration_ms)
        if not self.start_ms <= self.anchor_ms <= self.end_ms:
            raise ValueError("candidate anchor outside interval")
        for obs in self.observations:
            obs.validate(duration_ms)

    def to_dict(self) -> dict:
        return asdict(self)
