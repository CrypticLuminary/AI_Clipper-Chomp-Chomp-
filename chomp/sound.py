"""Editor-supplied local audio cues on the same source clock as the clip."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path

from .artifacts import source_identity
from .models import checked_span


@dataclass(frozen=True)
class SoundCue:
    path: str
    at_ms: int
    duration_ms: int
    offset_ms: int
    gain_db: float
    sha256: str


def read_sound_cues(path: Path | None, saved: list[dict] | None, source_duration_ms: int) -> tuple[SoundCue, ...]:
    from .media import probe

    folder = None
    rows = [] if saved is None else saved
    if path is not None:
        path = path.expanduser().resolve()
        folder = path.parent
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or set(data) != {"cues"}:
            raise ValueError("Sound cue file needs a cues array")
        rows = data["cues"]
    if not isinstance(rows, list):
        raise ValueError("Sound cues must be a list")

    assets = {}
    result = []
    required = {"path", "at_ms", "duration_ms"}
    allowed = required | {"offset_ms", "gain_db", "sha256"}
    for row in rows:
        if not isinstance(row, dict) or not required <= row.keys() <= allowed:
            raise ValueError("A sound cue needs path, at_ms and duration_ms")
        at, length, offset = row["at_ms"], row["duration_ms"], row.get("offset_ms", 0)
        gain = row.get("gain_db", -18)
        if any(type(value) is not int for value in (at, length, offset)):
            raise ValueError("Sound cue times must be integer milliseconds")
        checked_span(at, at + length, source_duration_ms)
        if type(gain) not in (int, float) or not -60 <= gain <= 12:
            raise ValueError("Sound cue gain must be between -60 and +12 dB")
        if not isinstance(row["path"], str) or not row["path"].strip():
            raise ValueError("Provide the path to a local audio file")
        asset = Path(row["path"]).expanduser()
        if not asset.is_absolute() and folder is None:
            raise ValueError("Saved sound cue paths must be absolute")
        asset = (folder / asset).resolve() if folder is not None else asset.resolve()
        if not asset.is_file():
            raise ValueError(f"Sound cue file is missing: {asset}")
        if asset not in assets:
            assets[asset] = probe(asset)["duration_ms"], source_identity(asset).removeprefix("sha256:")
        duration, digest = assets[asset]
        checked_span(offset, offset + length, duration)
        if folder is None and row.get("sha256") != digest:
            raise ValueError(f"Sound cue asset changed. Supply the revised schedule explicitly: {asset}")
        result.append(SoundCue(str(asset), at, length, offset, float(gain), digest))
    return tuple(sorted(result, key=lambda cue: cue.at_ms))


def cues_in_range(cues: tuple[SoundCue, ...], start_ms: int, end_ms: int) -> tuple[SoundCue, ...]:
    result = []
    for cue in cues:
        left = max(start_ms, cue.at_ms)
        right = min(end_ms, cue.at_ms + cue.duration_ms)
        if left < right:
            result.append(replace(cue, at_ms=left, duration_ms=right - left,
                                  offset_ms=cue.offset_ms + left - cue.at_ms))
    return tuple(result)
