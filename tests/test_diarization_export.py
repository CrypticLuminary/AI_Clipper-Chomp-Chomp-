"""Tests for diarization export safety and identity overwrite prevention (VOICE-03-FIX02).

Proves that:
1. A new output directory works.
2. An existing manually confirmed identity file remains byte-for-byte unchanged.
3. A rejected rerun does not overwrite existing speaker turns.
4. The error clearly identifies the file conflict.
5. A different output directory allows another run.
6. Concurrent creation cannot silently replace an existing identity file.
"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chomp.models import SpeakerTurn
from chomp.diarization.core import DiarizationConfig, DiarizationResult
from chomp.diarization.identity import SpeakerIdentityMap
from chomp.diarization.integration import diarize_and_export


@pytest.fixture
def dummy_media(tmp_path: Path) -> Path:
    """Create a dummy media file."""
    media = tmp_path / "source.mp4"
    media.write_bytes(b"dummy media content")
    return media


@pytest.fixture
def mock_diarization_result() -> DiarizationResult:
    """Sample diarization result with 2 speakers."""
    return DiarizationResult(
        turns=[
            SpeakerTurn(1000, 3000, "SPEAKER_00"),
            SpeakerTurn(4000, 7000, "SPEAKER_01"),
        ],
        num_speakers_detected=2,
        method="mock_backend",
    )


class TestDiarizationExportSafety:
    """Test suite for IdentityGuard: fail-closed identity overwrite prevention."""

    def test_new_output_directory_works(
        self, tmp_path: Path, dummy_media: Path, mock_diarization_result: DiarizationResult
    ):
        """A new output directory exports turns and identity map successfully."""
        out_dir = tmp_path / "fresh_export"

        with patch("chomp.diarization.integration.check_backend_available", return_value=True), \
             patch("chomp.diarization.integration.convert_to_wav") as mock_convert, \
             patch("chomp.diarization.integration.diarize_audio", return_value=mock_diarization_result) as mock_diarize:
            result, turns_path, id_path = diarize_and_export(
                dummy_media,
                out_dir,
                duration_ms=8000,
            )

        assert mock_convert.called
        assert mock_diarize.called
        assert turns_path == out_dir / "speaker_turns.json"
        assert id_path == out_dir / "speaker_identity_map.json"
        assert turns_path.is_file()
        assert id_path.is_file()

        # Verify turns content
        turns_data = json.loads(turns_path.read_text(encoding="utf-8"))
        assert len(turns_data["turns"]) == 2
        assert turns_data["turns"][0]["speaker_id"] == "SPEAKER_00"

        # Verify identity map is a template (unconfirmed by default)
        id_data = json.loads(id_path.read_text(encoding="utf-8"))
        assert id_data["confirmed"] is False
        assert "SPEAKER_00" in id_data["mappings"]
        assert "SPEAKER_01" in id_data["mappings"]

    def test_existing_confirmed_identity_remains_byte_for_byte_unchanged(
        self, tmp_path: Path, dummy_media: Path, mock_diarization_result: DiarizationResult
    ):
        """An existing manually confirmed identity file remains byte-for-byte unchanged."""
        out_dir = tmp_path / "existing_confirmed"
        out_dir.mkdir(parents=True)
        identity_path = out_dir / "speaker_identity_map.json"

        # Manually confirmed mapping by human reviewer
        confirmed_map = {
            "mappings": {
                "SPEAKER_00": "MainStreamerAlex",
                "SPEAKER_01": "TeammateJordan",
            },
            "main_speaker_id": "SPEAKER_00",
            "confirmed": True,
            "confirmation_method": "manual_review",
            "notes": "Verified streamer voice against facecam intro at 00:01:20",
        }
        identity_path.write_text(
            json.dumps(confirmed_map, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        original_bytes = identity_path.read_bytes()

        with patch("chomp.diarization.integration.check_backend_available", return_value=True), \
             patch("chomp.diarization.integration.convert_to_wav") as mock_convert, \
             patch("chomp.diarization.integration.diarize_audio", return_value=mock_diarization_result) as mock_diarize:
            with pytest.raises(FileExistsError) as exc_info:
                diarize_and_export(dummy_media, out_dir, duration_ms=8000)

        # Fail-closed before expensive audio conversion/diarization
        assert not mock_convert.called
        assert not mock_diarize.called

        # The confirmed identity file must remain byte-for-byte identical
        assert identity_path.read_bytes() == original_bytes

        # The error must explain the conflict
        err = str(exc_info.value)
        assert str(identity_path) in err
        assert "already exists" in err.lower()
        assert "different output directory" in err.lower()

    def test_rejected_rerun_does_not_overwrite_existing_speaker_turns(
        self, tmp_path: Path, dummy_media: Path
    ):
        """A rejected rerun does not overwrite existing speaker_turns.json."""
        out_dir = tmp_path / "turns_protection"
        out_dir.mkdir(parents=True)
        identity_path = out_dir / "speaker_identity_map.json"
        turns_path = out_dir / "speaker_turns.json"

        # Existing files from a previous run
        identity_path.write_text('{"confirmed": true, "main_speaker_id": "S0"}', encoding="utf-8")
        original_turns_bytes = (
            b'{\n  "turns": [\n    {\n      "start_ms": 100,\n      "end_ms": 900,\n'
            b'      "speaker_id": "PRESERVED_ORIGINAL_TURN"\n    }\n  ]\n}'
        )
        turns_path.write_bytes(original_turns_bytes)

        # Malicious / different new result
        new_result = DiarizationResult(
            turns=[SpeakerTurn(0, 5000, "OVERWRITE_ATTEMPT")],
            num_speakers_detected=1,
            method="new_run",
        )

        with patch("chomp.diarization.integration.check_backend_available", return_value=True), \
             patch("chomp.diarization.integration.convert_to_wav"), \
             patch("chomp.diarization.integration.diarize_audio", return_value=new_result):
            with pytest.raises(FileExistsError):
                diarize_and_export(dummy_media, out_dir, duration_ms=8000)

        # Existing speaker turns file must be preserved untouched
        assert turns_path.read_bytes() == original_turns_bytes

    def test_error_clearly_identifies_file_conflict(
        self, tmp_path: Path, dummy_media: Path
    ):
        """The error clearly identifies the exact conflicting file and suggests an alternative."""
        out_dir = tmp_path / "conflict_dir"
        out_dir.mkdir(parents=True)
        id_file = out_dir / "speaker_identity_map.json"
        id_file.write_text("{}", encoding="utf-8")

        with patch("chomp.diarization.integration.check_backend_available", return_value=True):
            with pytest.raises(FileExistsError) as exc_info:
                diarize_and_export(dummy_media, out_dir, duration_ms=5000)

        msg = str(exc_info.value)
        assert str(id_file) in msg
        assert "already exists" in msg.lower()
        assert "choose a different output directory" in msg.lower()

    def test_different_output_directory_allows_another_run(
        self, tmp_path: Path, dummy_media: Path, mock_diarization_result: DiarizationResult
    ):
        """A different output directory allows another run without conflict."""
        out_dir_1 = tmp_path / "run_one"
        out_dir_2 = tmp_path / "run_two"

        with patch("chomp.diarization.integration.check_backend_available", return_value=True), \
             patch("chomp.diarization.integration.convert_to_wav"), \
             patch("chomp.diarization.integration.diarize_audio", return_value=mock_diarization_result):
            # Run 1 succeeds
            res1, turns1, id1 = diarize_and_export(dummy_media, out_dir_1, duration_ms=5000)
            assert turns1.is_file()
            assert id1.is_file()

            # Rerunning on out_dir_1 is rejected
            with pytest.raises(FileExistsError):
                diarize_and_export(dummy_media, out_dir_1, duration_ms=5000)

            # Running on a distinct out_dir_2 succeeds cleanly
            res2, turns2, id2 = diarize_and_export(dummy_media, out_dir_2, duration_ms=5000)
            assert turns2.is_file()
            assert id2.is_file()
            assert turns1 != turns2
            assert id1 != id2

    def test_concurrent_creation_cannot_silently_replace_existing_identity_file(
        self, tmp_path: Path
    ):
        """Concurrent calls to SpeakerIdentityMap.save() on the same path cannot overwrite."""
        target_path = tmp_path / "concurrent" / "speaker_identity_map.json"

        maps = [
            SpeakerIdentityMap(
                mappings={f"SPEAKER_{i:02d}": f"User_{i}"},
                main_speaker_id=f"SPEAKER_{i:02d}",
                confirmed=True,
                notes=f"Created by thread {i}",
            )
            for i in range(10)
        ]

        successes = []
        failures = []

        def worker(m: SpeakerIdentityMap):
            try:
                m.save(target_path)
                return ("success", m.notes)
            except FileExistsError as e:
                return ("failure", str(e))

        # Launch all 10 workers simultaneously
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, m) for m in maps]
            for f in concurrent.futures.as_completed(futures):
                status, info = f.result()
                if status == "success":
                    successes.append(info)
                else:
                    failures.append(info)

        # Exactly ONE process must succeed; exactly 9 must fail with FileExistsError
        assert len(successes) == 1
        assert len(failures) == 9

        # The file on disk must be valid JSON matching the sole winner
        winner_data = json.loads(target_path.read_text(encoding="utf-8"))
        assert winner_data["notes"] == successes[0]

    def test_save_method_raises_file_exists_error_on_collision(self, tmp_path: Path):
        """Direct call to SpeakerIdentityMap.save() fails if file already exists."""
        target_path = tmp_path / "map.json"
        target_path.write_text("existing content", encoding="utf-8")

        m = SpeakerIdentityMap(mappings={"S0": "Streamer"})
        with pytest.raises(FileExistsError) as exc_info:
            m.save(target_path)

        assert "already exists" in str(exc_info.value).lower()
        # Verify existing content unchanged
        assert target_path.read_text(encoding="utf-8") == "existing content"

    def test_existing_turns_with_no_identity_map_rejected(
        self, tmp_path: Path, dummy_media: Path, mock_diarization_result: DiarizationResult
    ):
        """Preflight rejects export when speaker_turns.json exists without identity map."""
        out_dir = tmp_path / "turns_only_dir"
        out_dir.mkdir(parents=True)
        turns_path = out_dir / "speaker_turns.json"
        identity_path = out_dir / "speaker_identity_map.json"

        original_turns_content = b'{\n  "turns": [\n    {"speaker_id": "PRESERVED_TURN"}\n  ]\n}'
        turns_path.write_bytes(original_turns_content)

        assert not identity_path.exists()

        with patch("chomp.diarization.integration.check_backend_available", return_value=True), \
             patch("chomp.diarization.integration.convert_to_wav") as mock_convert, \
             patch("chomp.diarization.integration.diarize_audio", return_value=mock_diarization_result) as mock_diarize:
            with pytest.raises(FileExistsError) as exc_info:
                diarize_and_export(dummy_media, out_dir, duration_ms=5000)

        # Preflight reject: no expensive FFmpeg conversion or inference
        assert not mock_convert.called
        assert not mock_diarize.called

        # Existing turns file remains byte-for-byte identical
        assert turns_path.read_bytes() == original_turns_content
        # Identity file is not created
        assert not identity_path.exists()

        # Actionable error message naming the conflicting turns file
        err = str(exc_info.value)
        assert str(turns_path) in err
        assert "already exists" in err.lower()
        assert "different output directory" in err.lower()

    def test_existing_turns_and_existing_identity_map_rejected(
        self, tmp_path: Path, dummy_media: Path, mock_diarization_result: DiarizationResult
    ):
        """Preflight rejects export when both turns and identity files already exist."""
        out_dir = tmp_path / "both_exist_dir"
        out_dir.mkdir(parents=True)
        turns_path = out_dir / "speaker_turns.json"
        identity_path = out_dir / "speaker_identity_map.json"

        original_turns = b'{"turns": "original_turns_content"}'
        original_identity = b'{"mappings": "original_identity_content"}'
        turns_path.write_bytes(original_turns)
        identity_path.write_bytes(original_identity)

        with patch("chomp.diarization.integration.check_backend_available", return_value=True), \
             patch("chomp.diarization.integration.convert_to_wav") as mock_convert, \
             patch("chomp.diarization.integration.diarize_audio", return_value=mock_diarization_result) as mock_diarize:
            with pytest.raises(FileExistsError) as exc_info:
                diarize_and_export(dummy_media, out_dir, duration_ms=5000)

        # Preflight reject: no conversion or inference
        assert not mock_convert.called
        assert not mock_diarize.called

        # Both files remain byte-for-byte identical
        assert turns_path.read_bytes() == original_turns
        assert identity_path.read_bytes() == original_identity

        # Actionable error message naming the conflicts
        err = str(exc_info.value)
        assert str(turns_path) in err
        assert str(identity_path) in err
        assert "already exists" in err.lower()
        assert "different output directory" in err.lower()

    def test_turns_file_created_between_preflight_and_turns_write_cannot_be_overwritten(
        self, tmp_path: Path, dummy_media: Path, mock_diarization_result: DiarizationResult
    ):
        """A turns file created between preflight and exclusive turns write is not overwritten."""
        out_dir = tmp_path / "late_race_dir"
        out_dir.mkdir(parents=True)
        turns_path = out_dir / "speaker_turns.json"
        identity_path = out_dir / "speaker_identity_map.json"

        concurrent_content = b'{"turns": "concurrent_race_winner_content"}'

        def simulate_late_arrival(*args, **kwargs):
            # Simulate a concurrent process creating speaker_turns.json during diarization
            turns_path.write_bytes(concurrent_content)
            return mock_diarization_result

        with patch("chomp.diarization.integration.check_backend_available", return_value=True), \
             patch("chomp.diarization.integration.convert_to_wav"), \
             patch("chomp.diarization.integration.diarize_audio", side_effect=simulate_late_arrival):
            with pytest.raises(FileExistsError) as exc_info:
                diarize_and_export(dummy_media, out_dir, duration_ms=5000)

        # The existing turns file was NOT overwritten
        assert turns_path.read_bytes() == concurrent_content

        # The identity map template was already written before turns write failed
        # (documenting the non-atomic export limitation)
        assert identity_path.is_file()

        # Actionable error message naming the conflicting turns file
        err = str(exc_info.value)
        assert str(turns_path) in err
        assert "already exists" in err.lower()
        assert "different output directory" in err.lower()
