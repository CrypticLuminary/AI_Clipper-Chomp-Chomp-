"""Tests for the speaker diarization subsystem.

These tests cover:
- Core diarization logic with mocked models (sherpa-onnx and resemblyzer)
- Energy-based VAD
- Speaker identity mapping
- Integration with existing pipeline contracts
- Edge cases: silence, single speaker, overlapping voices, short audio
- JSON contract compatibility

All tests run without heavyweight diarization dependencies (resemblyzer,
sherpa-onnx, scikit-learn). Model-dependent code is mocked.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import numpy as np

from chomp.models import SpeakerTurn, TranscriptSegment
from chomp.speakers import (
    attribute_segments,
    read_speaker_turns,
    speaker_inventory,
    candidate_speaker_context,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_audio_16k():
    """Generate a 10-second mono audio signal at 16 kHz with two 'speakers'."""
    sr = 16000
    duration = 10.0
    t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
    audio = np.zeros_like(t)
    # 'Speaker A': 0-3s at 200 Hz
    mask_a = t < 3.0
    audio[mask_a] = 0.5 * np.sin(2 * np.pi * 200 * t[mask_a])
    # Silence: 3-4s
    # 'Speaker B': 4-7s at 350 Hz
    mask_b = (t >= 4.0) & (t < 7.0)
    audio[mask_b] = 0.5 * np.sin(2 * np.pi * 350 * t[mask_b])
    # 'Speaker A' again: 7.5-10s at 200 Hz
    mask_a2 = t >= 7.5
    audio[mask_a2] = 0.5 * np.sin(2 * np.pi * 200 * t[mask_a2])
    return audio, sr


@pytest.fixture
def silence_audio():
    """10 seconds of silence."""
    return np.zeros(160000, dtype=np.float32), 16000


@pytest.fixture
def short_audio():
    """0.1 seconds of audio — too short for useful diarization."""
    sr = 16000
    t = np.linspace(0, 0.1, int(sr * 0.1), dtype=np.float32)
    return 0.5 * np.sin(2 * np.pi * 300 * t), sr


@pytest.fixture
def single_speaker_audio():
    """5 seconds of continuous single-speaker tone."""
    sr = 16000
    t = np.linspace(0, 5.0, int(sr * 5.0), dtype=np.float32)
    return 0.5 * np.sin(2 * np.pi * 250 * t), sr


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    """Write a numpy float32 array as a 16-bit WAV file."""
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


# ===================================================================
# Energy VAD tests
# ===================================================================

class TestEnergyVAD:
    def test_detects_speech_in_tone(self, sample_audio_16k):
        from chomp.diarization.core import _energy_vad
        audio, sr = sample_audio_16k
        segments = _energy_vad(audio, sr)
        assert len(segments) >= 2, f"Expected >=2 segments, got {len(segments)}"
        assert segments[0][0] < 500  # first segment near start
        for s, e in segments:
            assert e > s

    def test_silence_produces_no_segments(self, silence_audio):
        from chomp.diarization.core import _energy_vad
        audio, sr = silence_audio
        assert _energy_vad(audio, sr) == []

    def test_short_audio(self, short_audio):
        from chomp.diarization.core import _energy_vad
        audio, sr = short_audio
        assert isinstance(_energy_vad(audio, sr), list)

    def test_empty_audio(self):
        from chomp.diarization.core import _energy_vad
        assert _energy_vad(np.array([], dtype=np.float32), 16000) == []

    def test_segments_ordered_nonoverlapping(self, sample_audio_16k):
        from chomp.diarization.core import _energy_vad
        audio, sr = sample_audio_16k
        segments = _energy_vad(audio, sr)
        for i in range(len(segments) - 1):
            assert segments[i][1] <= segments[i + 1][0]

    def test_min_speech_ms_filtering(self, sample_audio_16k):
        from chomp.diarization.core import _energy_vad
        audio, sr = sample_audio_16k
        strict = _energy_vad(audio, sr, min_speech_ms=2000)
        loose = _energy_vad(audio, sr, min_speech_ms=100)
        assert len(strict) <= len(loose)


# ===================================================================
# Merge consecutive turns
# ===================================================================

class TestMergeConsecutiveTurns:
    def test_merge_same_speaker_small_gap(self):
        from chomp.diarization.core import _merge_consecutive_turns
        turns = [
            SpeakerTurn(0, 3000, "SPEAKER_00"),
            SpeakerTurn(3200, 5000, "SPEAKER_00"),
        ]
        merged = _merge_consecutive_turns(turns)
        assert len(merged) == 1
        assert merged[0] == SpeakerTurn(0, 5000, "SPEAKER_00")

    def test_no_merge_different_speakers(self):
        from chomp.diarization.core import _merge_consecutive_turns
        turns = [
            SpeakerTurn(0, 3000, "SPEAKER_00"),
            SpeakerTurn(3200, 5000, "SPEAKER_01"),
        ]
        assert len(_merge_consecutive_turns(turns)) == 2

    def test_no_merge_large_gap(self):
        from chomp.diarization.core import _merge_consecutive_turns
        turns = [
            SpeakerTurn(0, 3000, "SPEAKER_00"),
            SpeakerTurn(4000, 6000, "SPEAKER_00"),
        ]
        assert len(_merge_consecutive_turns(turns)) == 2

    def test_empty(self):
        from chomp.diarization.core import _merge_consecutive_turns
        assert _merge_consecutive_turns([]) == []

    def test_single_turn(self):
        from chomp.diarization.core import _merge_consecutive_turns
        turns = [SpeakerTurn(0, 3000, "SPEAKER_00")]
        assert _merge_consecutive_turns(turns) == turns


# ===================================================================
# Validate turns
# ===================================================================

class TestValidateTurns:
    def test_clamps_to_duration(self):
        from chomp.diarization.core import _validate_turns
        valid = _validate_turns([SpeakerTurn(0, 15000, "SPEAKER_00")], 10000)
        assert len(valid) == 1
        assert valid[0].end_ms == 10000

    def test_removes_zero_duration(self):
        from chomp.diarization.core import _validate_turns
        assert _validate_turns([SpeakerTurn(5000, 5000, "SPEAKER_00")], 10000) == []

    def test_sorts_by_start(self):
        from chomp.diarization.core import _validate_turns
        valid = _validate_turns([
            SpeakerTurn(5000, 8000, "SPEAKER_01"),
            SpeakerTurn(1000, 3000, "SPEAKER_00"),
        ], 10000)
        assert valid[0].start_ms == 1000

    def test_negative_start_clamped(self):
        from chomp.diarization.core import _validate_turns
        valid = _validate_turns([SpeakerTurn(-100, 3000, "SPEAKER_00")], 10000)
        assert valid[0].start_ms == 0


# ===================================================================
# DiarizationConfig
# ===================================================================

class TestDiarizationConfig:
    def test_defaults(self):
        from chomp.diarization.core import DiarizationConfig
        c = DiarizationConfig()
        assert c.num_speakers is None
        assert c.min_segment_ms == 500
        assert c.max_speakers == 6
        assert c.sample_rate == 16000
        assert c.backend == "auto"

    def test_custom(self):
        from chomp.diarization.core import DiarizationConfig
        c = DiarizationConfig(num_speakers=3, backend="resemblyzer")
        assert c.num_speakers == 3
        assert c.backend == "resemblyzer"

    def test_frozen(self):
        from chomp.diarization.core import DiarizationConfig
        with pytest.raises(AttributeError):
            DiarizationConfig().num_speakers = 5  # type: ignore[misc]


# ===================================================================
# DiarizationResult
# ===================================================================

class TestDiarizationResult:
    def test_to_json_dict(self):
        from chomp.diarization.core import DiarizationResult
        result = DiarizationResult(
            turns=[SpeakerTurn(0, 3000, "SPEAKER_00"), SpeakerTurn(3000, 6000, "SPEAKER_01")],
            num_speakers_detected=2, method="resemblyzer_spectral",
        )
        d = result.to_json_dict()
        assert d["num_speakers_detected"] == 2
        assert len(d["turns"]) == 2
        assert d["turns"][0]["speaker_id"] == "SPEAKER_00"
        assert d["turns"][0]["start_ms"] == 0
        assert d["turns"][0]["end_ms"] == 3000

    def test_json_compatible_with_read_speaker_turns(self, tmp_path):
        """DiarizationResult JSON is readable by the existing read_speaker_turns."""
        from chomp.diarization.core import DiarizationResult
        result = DiarizationResult(
            turns=[SpeakerTurn(1000, 4000, "SPEAKER_00"), SpeakerTurn(5000, 9000, "SPEAKER_01")],
            num_speakers_detected=2, method="test",
        )
        path = tmp_path / "turns.json"
        path.write_text(json.dumps(result.to_json_dict(), indent=2), encoding="utf-8")
        loaded = read_speaker_turns(path, 10000)
        assert len(loaded) == 2
        assert loaded[0].speaker_id == "SPEAKER_00"
        assert loaded[1].speaker_id == "SPEAKER_01"

    def test_empty_result(self):
        from chomp.diarization.core import DiarizationResult
        d = DiarizationResult(turns=[], num_speakers_detected=0, method="empty").to_json_dict()
        assert d["turns"] == []


# ===================================================================
# Speaker identity mapping
# ===================================================================

class TestSpeakerIdentityMap:
    def test_creation_and_lookup(self):
        from chomp.diarization.identity import SpeakerIdentityMap
        m = SpeakerIdentityMap(
            mappings={"SPEAKER_00": "Streamer", "SPEAKER_01": "Teammate"},
            main_speaker_id="SPEAKER_00", confirmed=True,
        )
        assert m.get_display_name("SPEAKER_00") == "Streamer"
        assert m.get_display_name("SPEAKER_02") == "SPEAKER_02"
        assert m.is_main_speaker("SPEAKER_00")
        assert not m.is_main_speaker("SPEAKER_01")

    def test_unconfirmed_not_main(self):
        from chomp.diarization.identity import SpeakerIdentityMap
        m = SpeakerIdentityMap(
            mappings={"SPEAKER_00": "Maybe"}, main_speaker_id="SPEAKER_00",
            confirmed=False,
        )
        assert not m.is_main_speaker("SPEAKER_00")

    def test_validation_warnings(self):
        from chomp.diarization.identity import SpeakerIdentityMap
        m = SpeakerIdentityMap(
            mappings={"SPEAKER_00": "Streamer"},
            main_speaker_id="SPEAKER_99", confirmed=False,
        )
        w = m.validate()
        assert any("not in mappings" in x for x in w)
        assert any("not confirmed" in x for x in w)

    def test_save_and_load(self, tmp_path):
        from chomp.diarization.identity import SpeakerIdentityMap
        m = SpeakerIdentityMap(
            mappings={"SPEAKER_00": "Streamer"}, main_speaker_id="SPEAKER_00",
            confirmed=True, confirmation_method="manual_review", notes="Verified",
        )
        path = tmp_path / "id.json"
        m.save(path)
        loaded = SpeakerIdentityMap.load(path)
        assert loaded.mappings == m.mappings
        assert loaded.confirmed is True

    def test_round_trip_json(self):
        from chomp.diarization.identity import SpeakerIdentityMap
        m = SpeakerIdentityMap(
            mappings={"SPEAKER_00": "Alice"}, main_speaker_id="SPEAKER_00",
            confirmed=True,
        )
        assert SpeakerIdentityMap.from_json_dict(m.to_json_dict()).mappings == m.mappings


class TestBuildIdentityMap:
    def test_basic(self):
        from chomp.diarization.identity import build_identity_map
        m = build_identity_map(["SPEAKER_00", "SPEAKER_01"])
        assert set(m.mappings.keys()) == {"SPEAKER_00", "SPEAKER_01"}
        assert m.confirmed is False

    def test_with_main_speaker(self):
        from chomp.diarization.identity import build_identity_map
        m = build_identity_map(["SPEAKER_00", "SPEAKER_01"], main_speaker_id="SPEAKER_00", confirmed=True)
        assert m.main_speaker_id == "SPEAKER_00"

    def test_invalid_main_speaker_rejected(self):
        from chomp.diarization.identity import build_identity_map
        with pytest.raises(ValueError, match="not found"):
            build_identity_map(["SPEAKER_00"], main_speaker_id="SPEAKER_99")


# ===================================================================
# Backend availability
# ===================================================================

class TestBackendAvailability:
    def test_check_returns_bool(self):
        from chomp.diarization.core import check_backend_available
        assert isinstance(check_backend_available(), bool)

    def test_reason_is_string_or_none(self):
        from chomp.diarization.core import BACKEND_UNAVAILABLE_REASON
        assert BACKEND_UNAVAILABLE_REASON is None or isinstance(BACKEND_UNAVAILABLE_REASON, str)

    def test_unavailable_when_nothing_installed(self):
        from chomp.diarization import core
        with patch.object(core, "_SHERPA_AVAILABLE", False), \
             patch.object(core, "sherpa_onnx", None), \
             patch.object(core, "_RESEMBLYZER_AVAILABLE", False), \
             patch.object(core, "VoiceEncoder", None), \
             patch.object(core, "_SKLEARN_AVAILABLE", False), \
             patch.object(core, "SpectralClustering", None):
            assert not core.check_backend_available()

    def test_available_with_sherpa_only(self):
        from chomp.diarization import core
        with patch.object(core, "_SHERPA_AVAILABLE", True), \
             patch.object(core, "_RESEMBLYZER_AVAILABLE", False), \
             patch.object(core, "_SKLEARN_AVAILABLE", False):
            assert core.check_backend_available()

    def test_available_with_resemblyzer_and_sklearn(self):
        from chomp.diarization import core
        with patch.object(core, "_SHERPA_AVAILABLE", False), \
             patch.object(core, "_RESEMBLYZER_AVAILABLE", True), \
             patch.object(core, "_SKLEARN_AVAILABLE", True):
            assert core.check_backend_available()

    def test_resemblyzer_alone_not_enough(self):
        from chomp.diarization import core
        with patch.object(core, "_SHERPA_AVAILABLE", False), \
             patch.object(core, "_RESEMBLYZER_AVAILABLE", True), \
             patch.object(core, "_SKLEARN_AVAILABLE", False):
            assert not core.check_backend_available()


# ===================================================================
# Mocked diarization (resemblyzer backend)
# ===================================================================

class TestDiarizeResemblyzerMocked:
    """Integration tests using mocked resemblyzer and sklearn."""

    def _mock_encoder(self, num_speakers: int = 2):
        """Create a mock VoiceEncoder with distinct embeddings per speaker."""
        encoder = MagicMock()
        dim = 256
        rng = np.random.RandomState(42)
        centroids = {i: rng.randn(dim).astype(np.float32) for i in range(num_speakers)}
        call_count = [0]

        def embed(audio, rate=1.6):
            idx = call_count[0]
            call_count[0] += 1
            speaker = idx % num_speakers
            emb = centroids[speaker] + rng.randn(dim).astype(np.float32) * 0.05
            return (emb / np.linalg.norm(emb)).astype(np.float32)

        encoder.embed_utterance = embed
        return encoder

    def test_full_pipeline_mocked(self, tmp_path, sample_audio_16k):
        from chomp.diarization.core import DiarizationConfig, diarize_audio

        audio, sr = sample_audio_16k
        wav_path = tmp_path / "test.wav"
        _write_wav(wav_path, audio, sr)

        config = DiarizationConfig(num_speakers=2, min_segment_ms=200, backend="resemblyzer")
        mock_enc = self._mock_encoder(2)

        mock_sc = MagicMock()
        mock_sc.return_value.fit_predict = lambda X: np.array([i % 2 for i in range(X.shape[0])])

        with patch("chomp.diarization.core._SHERPA_AVAILABLE", False), \
             patch("chomp.diarization.core._RESEMBLYZER_AVAILABLE", True), \
             patch("chomp.diarization.core._SKLEARN_AVAILABLE", True), \
             patch("chomp.diarization.core.VoiceEncoder", return_value=mock_enc), \
             patch("chomp.diarization.core.SpectralClustering", mock_sc):
            result = diarize_audio(wav_path, 10000, config)

        assert result.num_speakers_detected == 2
        assert "resemblyzer" in result.method
        assert len(result.turns) > 0
        for t in result.turns:
            assert 0 <= t.start_ms < t.end_ms <= 10000
            assert t.speaker_id.startswith("SPEAKER_")

    def test_silence_no_turns(self, tmp_path, silence_audio):
        from chomp.diarization.core import DiarizationConfig, diarize_audio

        audio, sr = silence_audio
        wav_path = tmp_path / "silence.wav"
        _write_wav(wav_path, audio, sr)

        with patch("chomp.diarization.core._SHERPA_AVAILABLE", False), \
             patch("chomp.diarization.core._RESEMBLYZER_AVAILABLE", True), \
             patch("chomp.diarization.core._SKLEARN_AVAILABLE", True), \
             patch("chomp.diarization.core.VoiceEncoder", MagicMock()), \
             patch("chomp.diarization.core.SpectralClustering", MagicMock()):
            result = diarize_audio(wav_path, 10000, DiarizationConfig(backend="resemblyzer"))
        assert result.turns == []
        assert result.num_speakers_detected == 0

    def test_single_speaker_fallback(self, tmp_path, single_speaker_audio):
        from chomp.diarization.core import DiarizationConfig, diarize_audio

        audio, sr = single_speaker_audio
        wav_path = tmp_path / "single.wav"
        _write_wav(wav_path, audio, sr)

        config = DiarizationConfig(min_utterance_ms=6000, backend="resemblyzer")
        mock_enc = self._mock_encoder(1)

        with patch("chomp.diarization.core._SHERPA_AVAILABLE", False), \
             patch("chomp.diarization.core._RESEMBLYZER_AVAILABLE", True), \
             patch("chomp.diarization.core._SKLEARN_AVAILABLE", True), \
             patch("chomp.diarization.core.VoiceEncoder", return_value=mock_enc), \
             patch("chomp.diarization.core.SpectralClustering", MagicMock()):
            result = diarize_audio(wav_path, 5000, config)

        assert result.num_speakers_detected == 1
        assert all(t.speaker_id == "SPEAKER_00" for t in result.turns)

    def test_rejects_when_unavailable(self, tmp_path, sample_audio_16k):
        from chomp.diarization.core import diarize_audio

        audio, sr = sample_audio_16k
        wav_path = tmp_path / "test.wav"
        _write_wav(wav_path, audio, sr)

        with patch("chomp.diarization.core._SHERPA_AVAILABLE", False), \
             patch("chomp.diarization.core.sherpa_onnx", None), \
             patch("chomp.diarization.core._RESEMBLYZER_AVAILABLE", False), \
             patch("chomp.diarization.core.VoiceEncoder", None), \
             patch("chomp.diarization.core._SKLEARN_AVAILABLE", False), \
             patch("chomp.diarization.core.SpectralClustering", None):
            with pytest.raises(RuntimeError, match="unavailable"):
                diarize_audio(wav_path, 10000)

    def test_missing_file(self):
        from chomp.diarization.core import diarize_audio
        with patch("chomp.diarization.core._SHERPA_AVAILABLE", False), \
             patch("chomp.diarization.core._RESEMBLYZER_AVAILABLE", True), \
             patch("chomp.diarization.core._SKLEARN_AVAILABLE", True), \
             patch("chomp.diarization.core.VoiceEncoder", MagicMock()), \
             patch("chomp.diarization.core.SpectralClustering", MagicMock()):
            with pytest.raises(FileNotFoundError):
                from chomp.diarization.core import DiarizationConfig
                diarize_audio(Path("/nonexistent.wav"), 10000, DiarizationConfig(backend="resemblyzer"))

    def test_invalid_backend(self, tmp_path, sample_audio_16k):
        from chomp.diarization.core import DiarizationConfig, diarize_audio
        audio, sr = sample_audio_16k
        wav_path = tmp_path / "test.wav"
        _write_wav(wav_path, audio, sr)
        with pytest.raises(ValueError, match="Unknown backend"):
            diarize_audio(wav_path, 10000, DiarizationConfig(backend="invalid"))


# ===================================================================
# Mocked diarization (sherpa-onnx backend)
# ===================================================================

class TestDiarizeSherpaMocked:
    """Integration tests for sherpa-onnx backend with mocked sherpa."""

    def test_sherpa_pipeline_mocked(self, tmp_path, sample_audio_16k):
        from chomp.diarization.core import DiarizationConfig, diarize_audio

        audio, sr = sample_audio_16k
        wav_path = tmp_path / "sherpa_test.wav"
        _write_wav(wav_path, audio, sr)

        # Mock segment
        class MockSeg:
            def __init__(self, start, end, speaker):
                self.start = start
                self.end = end
                self.speaker = speaker

        mock_segments = [
            MockSeg(0.5, 3.0, 0),
            MockSeg(4.0, 7.0, 1),
            MockSeg(7.5, 9.5, 0),
        ]

        mock_processed = MagicMock()
        mock_processed.sort_by_start_time.return_value = mock_segments

        mock_sd = MagicMock()
        mock_sd.sample_rate = 16000
        mock_sd.process.return_value = mock_processed

        mock_sherpa = MagicMock()
        mock_sherpa.OfflineSpeakerDiarization.return_value = mock_sd

        with patch("chomp.diarization.core._SHERPA_AVAILABLE", True), \
             patch("chomp.diarization.core.sherpa_onnx", mock_sherpa):
            result = diarize_audio(
                wav_path, 10000,
                DiarizationConfig(backend="sherpa")
            )

        assert result.num_speakers_detected == 2
        assert result.method == "sherpa_onnx"
        assert len(result.turns) == 3
        assert result.turns[0].speaker_id == "SPEAKER_00"
        assert result.turns[0].start_ms == 500
        assert result.turns[0].end_ms == 3000
        assert result.turns[1].speaker_id == "SPEAKER_01"
        assert result.turns[2].speaker_id == "SPEAKER_00"

    def test_sherpa_missing_raises(self, tmp_path, sample_audio_16k):
        from chomp.diarization.core import DiarizationConfig, diarize_audio

        audio, sr = sample_audio_16k
        wav_path = tmp_path / "test.wav"
        _write_wav(wav_path, audio, sr)

        with patch("chomp.diarization.core._SHERPA_AVAILABLE", False), \
             patch("chomp.diarization.core.sherpa_onnx", None):
            with pytest.raises(RuntimeError, match="sherpa-onnx"):
                diarize_audio(wav_path, 10000, DiarizationConfig(backend="sherpa"))


# ===================================================================
# Pipeline integration contracts
# ===================================================================

class TestPipelineIntegration:
    def test_diarization_turns_with_attribute_segments(self):
        """Diarization output works with the existing attribute_segments."""
        turns = [
            SpeakerTurn(0, 4000, "SPEAKER_00"),
            SpeakerTurn(4000, 8000, "SPEAKER_01"),
            SpeakerTurn(8000, 12000, "SPEAKER_00"),
        ]
        segments = [
            TranscriptSegment(1000, 3000, "Hello everyone"),
            TranscriptSegment(5000, 7000, "Nice shot"),
            TranscriptSegment(9000, 11000, "Back to me"),
        ]
        attributed = attribute_segments(segments, turns)
        assert attributed[0].speaker_id == "SPEAKER_00"
        assert attributed[1].speaker_id == "SPEAKER_01"
        assert attributed[2].speaker_id == "SPEAKER_00"

    def test_json_readable_by_read_speaker_turns(self, tmp_path):
        """JSON output matches the --speaker-turns contract."""
        data = {"turns": [
            {"start_ms": 1000, "end_ms": 4000, "speaker_id": "SPEAKER_00"},
            {"start_ms": 5000, "end_ms": 9000, "speaker_id": "SPEAKER_01"},
        ]}
        path = tmp_path / "turns.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        loaded = read_speaker_turns(path, 10000)
        assert len(loaded) == 2

    def test_speaker_inventory(self):
        """speaker_inventory works with diarization IDs."""
        segs = [
            TranscriptSegment(1000, 3000, "Hi", speaker_id="SPEAKER_00"),
            TranscriptSegment(5000, 7000, "Hey", speaker_id="SPEAKER_01"),
        ]
        turns = [SpeakerTurn(0, 4000, "SPEAKER_00"), SpeakerTurn(4000, 8000, "SPEAKER_01")]
        assert speaker_inventory(segs, turns, "SPEAKER_00") == ["SPEAKER_00", "SPEAKER_01"]
        with pytest.raises(ValueError, match="does not occur"):
            speaker_inventory(segs, turns, "SPEAKER_99")

    def test_candidate_speaker_context(self):
        """candidate_speaker_context works with diarization-attributed segments."""
        segs = [
            TranscriptSegment(1000, 3000, "Streamer", speaker_id="SPEAKER_00"),
            TranscriptSegment(5000, 7000, "Teammate", speaker_id="SPEAKER_01"),
        ]
        ctx = candidate_speaker_context(segs, 0, 8000, "SPEAKER_00")
        assert ctx[0]["role"] == "main_streamer"
        assert ctx[1]["role"] == "other_speaker"

    def test_never_assumes_speaker_00_is_streamer(self):
        """Without main_speaker_id, all speakers are 'unverified'."""
        segs = [
            TranscriptSegment(1000, 3000, "Someone", speaker_id="SPEAKER_00"),
            TranscriptSegment(5000, 7000, "Another", speaker_id="SPEAKER_01"),
        ]
        ctx = candidate_speaker_context(segs, 0, 8000, None)
        assert all(c["role"] == "unverified" for c in ctx)


# ===================================================================
# Edge cases
# ===================================================================

class TestEdgeCases:
    def test_speaker_unknown_valid(self):
        t = SpeakerTurn(1000, 3000, "SPEAKER_UNKNOWN")
        t.validate(5000)

    def test_overlapping_turns_conservative(self):
        """Overlapping turns: attribution should be conservative."""
        turns = [
            SpeakerTurn(0, 5000, "SPEAKER_00"),
            SpeakerTurn(3000, 8000, "SPEAKER_01"),
        ]
        segs = [TranscriptSegment(3500, 4500, "Overlapping")]
        assert attribute_segments(segs, turns)[0].speaker_id is None

    def test_empty_turns_preserves_segments(self):
        segs = [TranscriptSegment(1000, 3000, "Hello")]
        assert attribute_segments(segs, []) == segs

    def test_existing_speaker_id_preserved(self):
        """Pre-labeled segments must not be overwritten."""
        turns = [SpeakerTurn(0, 10000, "SPEAKER_01")]
        segs = [TranscriptSegment(1000, 3000, "Labeled", speaker_id="SPEAKER_00")]
        assert attribute_segments(segs, turns)[0].speaker_id == "SPEAKER_00"

    def test_many_speakers(self):
        turns = [SpeakerTurn(i * 1000, (i + 1) * 1000, f"SPEAKER_{i:02d}") for i in range(6)]
        segs = [TranscriptSegment(i * 1000 + 100, (i + 1) * 1000 - 100, f"S{i}") for i in range(6)]
        attributed = attribute_segments(segs, turns)
        for i, s in enumerate(attributed):
            assert s.speaker_id == f"SPEAKER_{i:02d}"

    def test_gap_between_turns_unattributed(self):
        turns = [SpeakerTurn(0, 3000, "SPEAKER_00"), SpeakerTurn(7000, 10000, "SPEAKER_01")]
        segs = [TranscriptSegment(4000, 6000, "In the gap")]
        assert attribute_segments(segs, turns)[0].speaker_id is None

    def test_integer_milliseconds(self):
        t = SpeakerTurn(1000, 3000, "SPEAKER_00")
        assert isinstance(t.start_ms, int)
        assert isinstance(t.end_ms, int)


# ===================================================================
# Integration module
# ===================================================================

class TestIntegrationModule:
    def test_merge_diarization_with_transcript(self):
        from chomp.diarization.integration import merge_diarization_with_transcript
        turns = [SpeakerTurn(0, 4000, "SPEAKER_00"), SpeakerTurn(4000, 8000, "SPEAKER_01")]
        segs = [TranscriptSegment(1000, 3000, "First"), TranscriptSegment(5000, 7000, "Second")]
        result = merge_diarization_with_transcript(turns, segs)
        assert result[0].speaker_id == "SPEAKER_00"
        assert result[1].speaker_id == "SPEAKER_01"

    def test_generate_pipeline_command(self):
        from chomp.diarization.integration import generate_pipeline_command
        cmd = generate_pipeline_command(
            Path("video.mp4"), Path("turns.json"), main_speaker_id="SPEAKER_00",
        )
        assert "--speaker-turns" in cmd
        assert "--main-speaker-id" in cmd
        assert "SPEAKER_00" in cmd

    def test_generate_command_no_main_speaker(self):
        from chomp.diarization.integration import generate_pipeline_command
        cmd = generate_pipeline_command(Path("video.mp4"), Path("turns.json"))
        assert "--main-speaker-id" not in cmd


# ===================================================================
# Audio loading
# ===================================================================

class TestAudioLoading:
    def test_load_wav(self, tmp_path):
        from chomp.diarization.core import load_audio
        sr = 16000
        audio = np.sin(np.linspace(0, 2 * np.pi * 440, sr, dtype=np.float32)) * 0.5
        wav_path = tmp_path / "test.wav"
        _write_wav(wav_path, audio, sr)
        loaded = load_audio(wav_path, sr)
        assert len(loaded) == sr
        assert loaded.dtype == np.float32

    def test_load_nonexistent(self):
        from chomp.diarization.core import load_audio
        with pytest.raises(FileNotFoundError):
            load_audio(Path("/does/not/exist.wav"))

    def test_stereo_mixed_to_mono(self, tmp_path):
        from chomp.diarization.core import load_audio
        sr = 16000
        n = sr
        left = (np.sin(np.linspace(0, 2 * np.pi * 300, n, dtype=np.float32)) * 16000).astype(np.int16)
        right = np.zeros(n, dtype=np.int16)
        interleaved = np.empty(2 * n, dtype=np.int16)
        interleaved[0::2] = left
        interleaved[1::2] = right
        wav_path = tmp_path / "stereo.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(interleaved.tobytes())
        audio = load_audio(wav_path, sr)
        assert audio.ndim == 1
        assert len(audio) == n

    def test_resampling(self, tmp_path):
        """Audio at 48 kHz should be resampled to 16 kHz."""
        from chomp.diarization.core import load_audio
        sr_src = 48000
        sr_tgt = 16000
        duration = 1.0
        audio = np.sin(np.linspace(0, 2 * np.pi * 200, int(sr_src * duration), dtype=np.float32)) * 0.5
        wav_path = tmp_path / "resample.wav"
        pcm = (audio * 32767).astype(np.int16)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr_src)
            wf.writeframes(pcm.tobytes())
        loaded = load_audio(wav_path, sr_tgt)
        expected_len = int(duration * sr_tgt)
        assert abs(len(loaded) - expected_len) <= 1


# ===================================================================
# Convert to WAV (FFmpeg)
# ===================================================================

class TestConvertToWav:
    @pytest.mark.skipif(
        __import__("shutil").which("ffmpeg") is None, reason="FFmpeg not installed",
    )
    def test_wav_to_wav(self, tmp_path):
        from chomp.diarization.core import convert_to_wav
        sr = 16000
        audio = (np.sin(np.linspace(0, 2 * np.pi * 440, sr, dtype=np.float32)) * 32000).astype(np.int16)
        src = tmp_path / "input.wav"
        with wave.open(str(src), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(audio.tobytes())
        dst = tmp_path / "output.wav"
        convert_to_wav(src, dst, sample_rate=16000)
        assert dst.exists()
        with wave.open(str(dst), "rb") as wf:
            assert wf.getframerate() == 16000
            assert wf.getnchannels() == 1


# ===================================================================
# CLI entry point tests
# ===================================================================

class TestDiarizationCLI:
    def test_help(self):
        from chomp.diarization.__main__ import main
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_backend_unavailable_fails(self, tmp_path):
        from chomp.diarization.__main__ import main
        dummy = tmp_path / "video.mp4"
        dummy.write_text("fake")
        with patch("chomp.diarization.__main__.check_backend_available", return_value=False):
            assert main(["--media", str(dummy)]) == 1

    def test_missing_media_fails(self):
        from chomp.diarization.__main__ import main
        with patch("chomp.diarization.__main__.check_backend_available", return_value=True):
            assert main(["--media", "/nonexistent/media.mp4"]) == 1

    def test_successful_run_mocked(self, tmp_path, sample_audio_16k):
        from chomp.diarization.__main__ import main
        from chomp.diarization.core import DiarizationResult
        audio, sr = sample_audio_16k
        wav_path = tmp_path / "input.wav"
        _write_wav(wav_path, audio, sr)
        out_dir = tmp_path / "out"

        mock_result = DiarizationResult(
            turns=[SpeakerTurn(0, 3000, "SPEAKER_00"), SpeakerTurn(4000, 7000, "SPEAKER_01")],
            num_speakers_detected=2,
            method="test_mock",
        )

        with patch("chomp.diarization.__main__.check_backend_available", return_value=True), \
             patch("chomp.diarization.__main__.diarize_and_export", return_value=(
                 mock_result, out_dir / "speaker_turns.json", out_dir / "speaker_identity_map.json"
             )):
            code = main(["--media", str(wav_path), "--output", str(out_dir)])
            assert code == 0

