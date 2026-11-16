"""Speaker identity mapping — explicit confirmation, never automatic.

SPEAKER_00, SPEAKER_01, etc. are anonymous cluster labels.  This module
provides a mechanism to map them to human-readable identities *after*
manual confirmation.  The pipeline never assumes SPEAKER_00 is the streamer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..artifacts import write_json


@dataclass
class SpeakerIdentityMap:
    """Maps anonymous diarization labels to confirmed identities.

    Example::

        identity_map = SpeakerIdentityMap(
            mappings={"SPEAKER_00": "Streamer", "SPEAKER_01": "Teammate"},
            main_speaker_id="SPEAKER_00",
            confirmed=True,
            confirmation_method="manual_review",
        )

    Attributes:
        mappings: Dict of anonymous_id -> human_readable_name.
        main_speaker_id: Which anonymous ID is the main streamer (if confirmed).
        confirmed: Whether a human has verified the mapping.
        confirmation_method: How the mapping was confirmed.
        notes: Free-form notes about the mapping quality.
    """
    mappings: dict[str, str] = field(default_factory=dict)
    main_speaker_id: str | None = None
    confirmed: bool = False
    confirmation_method: str = "unconfirmed"
    notes: str = ""

    def get_display_name(self, speaker_id: str) -> str:
        """Return the human-readable name if mapped, else the raw ID."""
        return self.mappings.get(speaker_id, speaker_id)

    def is_main_speaker(self, speaker_id: str) -> bool:
        """Check if a speaker is the confirmed main streamer."""
        if not self.confirmed or self.main_speaker_id is None:
            return False
        return speaker_id == self.main_speaker_id

    def validate(self) -> list[str]:
        """Return a list of validation warnings."""
        warnings: list[str] = []
        if self.main_speaker_id and self.main_speaker_id not in self.mappings:
            warnings.append(
                f"main_speaker_id '{self.main_speaker_id}' not in mappings"
            )
        if self.main_speaker_id and not self.confirmed:
            warnings.append(
                "main_speaker_id set but mapping not confirmed; "
                "speaker identification is unreliable"
            )
        return warnings

    def to_json_dict(self) -> dict:
        return {
            "mappings": self.mappings,
            "main_speaker_id": self.main_speaker_id,
            "confirmed": self.confirmed,
            "confirmation_method": self.confirmation_method,
            "notes": self.notes,
        }

    @classmethod
    def from_json_dict(cls, data: dict) -> SpeakerIdentityMap:
        if not isinstance(data, dict):
            raise ValueError("Speaker identity map must be an object")
        confirmed = data.get("confirmed", False)
        mappings = data.get("mappings", {})
        main_id = data.get("main_speaker_id")
        method = data.get("confirmation_method", "unconfirmed")
        notes = data.get("notes", "")
        if type(confirmed) is not bool:
            raise ValueError("Speaker identity confirmation must be true or false")
        if (not isinstance(mappings, dict) or any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
            for key, value in mappings.items()
        )):
            raise ValueError("Speaker identity mappings must contain nonempty strings")
        if main_id is not None and (not isinstance(main_id, str) or not main_id.strip()):
            raise ValueError("main_speaker_id must be an ID or null")
        if confirmed and (not main_id or main_id not in mappings):
            raise ValueError("A confirmed identity needs a matching main_speaker_id")
        if not isinstance(method, str) or not isinstance(notes, str):
            raise ValueError("Speaker identity notes and confirmation method must be text")
        return cls(
            mappings=mappings, main_speaker_id=main_id, confirmed=confirmed,
            confirmation_method=method, notes=notes,
        )

    def save(self, path: Path) -> None:
        """Write the identity map to a JSON file using exclusive creation.

        Fails closed with FileExistsError if the target file already exists,
        preventing accidental overwrite of human review or race conditions.

        Raises:
            FileExistsError: If the target file already exists.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_json(path, self.to_json_dict(), exclusive=True)
        except FileExistsError as exc:
            raise FileExistsError(
                f"Speaker identity map already exists at '{path}'. "
                "Cannot overwrite existing identity mappings. "
                "Please choose a different output directory."
            ) from exc

    @classmethod
    def load(cls, path: Path) -> SpeakerIdentityMap:
        """Read an identity map from a JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_json_dict(data)


def build_identity_map(
    speaker_ids: list[str],
    main_speaker_id: str | None = None,
    *,
    confirmed: bool = False,
    confirmation_method: str = "unconfirmed",
) -> SpeakerIdentityMap:
    """Create an identity map from a list of detected speaker IDs.

    This creates a *template* mapping where each speaker ID maps to itself.
    The user is expected to edit the mapping file to assign real names.

    If ``main_speaker_id`` is provided, it must be one of the ``speaker_ids``.
    Setting ``confirmed=False`` (default) means the mapping is a proposal,
    not a verified identification.
    """
    if main_speaker_id is not None and main_speaker_id not in speaker_ids:
        raise ValueError(
            f"main_speaker_id '{main_speaker_id}' not found in speaker_ids: "
            f"{speaker_ids}"
        )

    # Default mapping: ID -> ID (user edits to add real names)
    mappings = {sid: sid for sid in speaker_ids}

    return SpeakerIdentityMap(
        mappings=mappings,
        main_speaker_id=main_speaker_id,
        confirmed=confirmed,
        confirmation_method=confirmation_method,
        notes="Auto-generated template. Edit mappings to assign speaker names. "
              "Set confirmed=true after manual verification.",
    )
