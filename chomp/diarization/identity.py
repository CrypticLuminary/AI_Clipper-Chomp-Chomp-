"""Speaker identity mapping — explicit confirmation, never automatic.

SPEAKER_00, SPEAKER_01, etc. are anonymous cluster labels.  This module
provides a mechanism to map them to human-readable identities *after*
manual confirmation.  The pipeline never assumes SPEAKER_00 is the streamer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


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
        return cls(
            mappings=data.get("mappings", {}),
            main_speaker_id=data.get("main_speaker_id"),
            confirmed=data.get("confirmed", False),
            confirmation_method=data.get("confirmation_method", "unconfirmed"),
            notes=data.get("notes", ""),
        )

    def save(self, path: Path) -> None:
        """Write the identity map to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_json_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

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
