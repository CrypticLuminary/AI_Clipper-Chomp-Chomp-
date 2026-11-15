"""Core diarization engine with two backend options.

Primary backend: sherpa-onnx (Apache 2.0, ONNX Runtime, no PyTorch, ~34 MB models)
Fallback backend: resemblyzer (Apache 2.0, PyTorch, ~16 MB model)

Design decisions:
- sherpa-onnx provides a complete diarization pipeline (VAD + segmentation
  + embeddings + clustering) with native Windows support and CPU optimization.
- resemblyzer is a fallback for environments where sherpa-onnx is unavailable.
- Both backends produce anonymous speaker labels (SPEAKER_00, SPEAKER_01, …).
- Overlapping voices are handled conservatively by the downstream attribution
  logic in ``chomp.speakers.attribute_segments``.
- Silence is not assigned to any speaker.
- All timestamps are integer milliseconds on the source clock.

Model licensing:
- sherpa-onnx: Apache 2.0 (code + segmentation/embedding models)
- resemblyzer: Apache 2.0 (GE2E encoder, ~16 MB)
- scikit-learn: BSD (clustering, used only by resemblyzer backend)

Model downloads are NOT triggered in CI.  The user must install the optional
dependency and models manually.
"""
from __future__ import annotations

import logging
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..media import SOURCE_CLOCK_AUDIO_FILTER
from ..models import SpeakerTurn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend availability detection & module-level references (mock-friendly)
# ---------------------------------------------------------------------------

sherpa_onnx: Any = None
VoiceEncoder: Any = None
SpectralClustering: Any = None
AgglomerativeClustering: Any = None

_SHERPA_AVAILABLE = False
_RESEMBLYZER_AVAILABLE = False
_SKLEARN_AVAILABLE = False

try:
    import numpy as np
except ImportError:
    np = None

try:
    import sherpa_onnx as _sherpa  # type: ignore[import-untyped]
    sherpa_onnx = _sherpa
    _SHERPA_AVAILABLE = True
except ImportError:
    pass

try:
    from resemblyzer import VoiceEncoder as _VE  # type: ignore[import-untyped]
    VoiceEncoder = _VE
    _RESEMBLYZER_AVAILABLE = True
except ImportError:
    pass

try:
    from sklearn.cluster import (  # type: ignore[import-untyped]
        SpectralClustering as _SC,
        AgglomerativeClustering as _AC,
    )
    SpectralClustering = _SC
    AgglomerativeClustering = _AC
    _SKLEARN_AVAILABLE = True
except ImportError:
    pass


def check_backend_available(config: DiarizationConfig | None = None) -> bool:
    """Return True if at least one diarization backend can be used.

    If a config is provided:
    - For 'sherpa', returns True only if sherpa-onnx is installed AND valid model paths are configured.
    - For 'resemblyzer', returns True if resemblyzer + sklearn are installed.
    - For 'auto', returns True if either backend is fully ready to run.
    """
    if np is None:
        return False
    sherpa_pkg = _SHERPA_AVAILABLE or sherpa_onnx is not None
    resemblyzer_ready = (_RESEMBLYZER_AVAILABLE or VoiceEncoder is not None) and (
        _SKLEARN_AVAILABLE or SpectralClustering is not None
    )

    if config is not None:
        sherpa_ready = sherpa_pkg and _has_valid_sherpa_models(config)
        if config.backend == "sherpa":
            return sherpa_ready
        elif config.backend == "resemblyzer":
            return resemblyzer_ready
        else:  # auto
            return sherpa_ready or resemblyzer_ready

    return sherpa_pkg or resemblyzer_ready


def _get_backend_unavailable_reason() -> str | None:
    """Return a human-readable reason if no backend is available, else None."""
    if check_backend_available():
        return None
    parts = []
    if np is None:
        parts.append("NumPy not installed")
    if not _SHERPA_AVAILABLE:
        parts.append("sherpa-onnx not installed (pip install sherpa-onnx)")
    if not _RESEMBLYZER_AVAILABLE:
        parts.append("resemblyzer not installed")
    if not _SKLEARN_AVAILABLE:
        parts.append("scikit-learn not installed")
    return "; ".join(parts) + ".  Install with: pip install 'chomp-chomp[diarize]'"


BACKEND_UNAVAILABLE_REASON: str | None = _get_backend_unavailable_reason()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiarizationConfig:
    """Tunable parameters for the diarization engine.

    Attributes:
        num_speakers: Expected speaker count.  ``None`` = auto-detect (2–6).
        min_segment_ms: Ignore speech segments shorter than this.
        embedding_rate: Resemblyzer partial-utterance rate (embeddings/sec).
            Ignored by the sherpa-onnx backend.
        min_utterance_ms: Minimum length for a usable utterance embedding.
        overlap_tolerance_ms: Tolerance for near-touching segments.
        confidence_threshold: Minimum silhouette-style confidence to accept
            a cluster assignment.  Below this, warn about unreliable results.
        max_speakers: Upper bound for auto-detection.
        sample_rate: Expected input sample rate (16000 Hz standard).
        backend: Which backend to prefer: "auto", "sherpa", "resemblyzer".
        segmentation_model: Path to sherpa-onnx segmentation ONNX model.
        embedding_model: Path to sherpa-onnx speaker embedding ONNX model.
    """
    num_speakers: int | None = None
    min_segment_ms: int = 500
    embedding_rate: float = 1.6
    min_utterance_ms: int = 800
    overlap_tolerance_ms: int = 200
    confidence_threshold: float = 0.25
    max_speakers: int = 6
    sample_rate: int = 16000
    backend: str = "auto"
    segmentation_model: Path | str | None = None
    embedding_model: Path | str | None = None


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class DiarizationResult:
    """Output of the diarization engine."""
    turns: list[SpeakerTurn]
    num_speakers_detected: int
    method: str  # e.g. "sherpa_onnx", "resemblyzer_spectral"
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_json_dict(self) -> dict:
        """Serialize to the existing JSON contract."""
        return {
            "turns": [
                {
                    "start_ms": t.start_ms,
                    "end_ms": t.end_ms,
                    "speaker_id": t.speaker_id,
                }
                for t in self.turns
            ],
            "num_speakers_detected": self.num_speakers_detected,
            "method": self.method,
            "warnings": self.warnings,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Audio I/O
# ---------------------------------------------------------------------------

def load_audio(path: Path, sample_rate: int = 16000) -> np.ndarray:
    """Load a WAV file as a float32 mono numpy array at the target sample rate."""
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")
    return _load_audio_wav(path, sample_rate)


def _load_audio_wav(path: Path, sample_rate: int = 16000) -> np.ndarray:
    """Load a WAV file as a float32 mono numpy array."""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width == 2:
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        audio = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sample_width == 1:
        audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"Unsupported sample width: {sample_width}")

    # Mix to mono
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    # Resample if needed (linear interpolation — sufficient for speaker embedding)
    if sr != sample_rate:
        duration = len(audio) / sr
        target_len = int(duration * sample_rate)
        if target_len > 0:
            indices = np.linspace(0, len(audio) - 1, target_len)
            audio = np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)
        else:
            audio = np.array([], dtype=np.float32)

    return audio


def convert_to_wav(
    input_path: Path,
    output_path: Path,
    sample_rate: int = 16000,
    audio_stream: int = 0,
) -> Path:
    """Convert any media to 16 kHz mono WAV using FFmpeg."""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg is required for audio conversion")

    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error",
        "-i", str(input_path),
        "-map", f"0:a:{audio_stream}",
        "-af", SOURCE_CLOCK_AUDIO_FILTER,
        "-ac", "1",
        "-ar", str(sample_rate),
        "-sample_fmt", "s16",
        "-f", "wav",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed: {result.stderr.strip()}")
    return output_path


# ---------------------------------------------------------------------------
# Energy-based VAD (no external dependency)
# ---------------------------------------------------------------------------

def _energy_vad(
    audio: np.ndarray,
    sample_rate: int,
    frame_ms: int = 30,
    energy_threshold_db: float = -35.0,
    min_speech_ms: int = 300,
    min_silence_ms: int = 200,
) -> list[tuple[int, int]]:
    """Simple energy-based voice activity detection.

    Returns list of (start_ms, end_ms) for speech segments.
    """
    frame_samples = int(sample_rate * frame_ms / 1000)
    if frame_samples == 0 or len(audio) == 0:
        return []

    n_frames = len(audio) // frame_samples
    if n_frames == 0:
        return []

    energies = np.array([
        10 * np.log10(
            np.mean(audio[i * frame_samples:(i + 1) * frame_samples] ** 2) + 1e-10
        )
        for i in range(n_frames)
    ])

    e_max = float(np.max(energies))
    e_min = float(np.min(energies))

    if e_max - e_min > 12.0:
        noise_floor = float(np.percentile(energies, 10))
        thresh = max(energy_threshold_db, min(noise_floor + 8.0, e_max - 6.0))
        is_speech = energies > thresh
    else:
        is_speech = energies > energy_threshold_db

    segments: list[tuple[int, int]] = []
    start_frame: int | None = None
    for i, speech in enumerate(is_speech):
        if speech and start_frame is None:
            start_frame = i
        elif not speech and start_frame is not None:
            segments.append((start_frame, i))
            start_frame = None
    if start_frame is not None:
        segments.append((start_frame, n_frames))

    # Merge close segments (separated by short silence < min_silence_ms)
    min_silence_frames = max(1, min_silence_ms // frame_ms)
    merged: list[tuple[int, int]] = []
    for seg_start, seg_end in segments:
        if merged and seg_start - merged[-1][1] < min_silence_frames:
            merged[-1] = (merged[-1][0], seg_end)
        else:
            merged.append((seg_start, seg_end))

    # Filter short segments and convert to ms
    min_speech_frames = max(1, min_speech_ms // frame_ms)
    result: list[tuple[int, int]] = []
    for seg_start, seg_end in merged:
        if seg_end - seg_start >= min_speech_frames:
            result.append((seg_start * frame_ms, seg_end * frame_ms))

    return result


# ---------------------------------------------------------------------------
# sherpa-onnx backend
# ---------------------------------------------------------------------------

def _has_valid_sherpa_models(config: DiarizationConfig) -> bool:
    """Return True if both segmentation and embedding models are configured and exist."""
    if not config.segmentation_model or not config.embedding_model:
        return False
    seg = Path(config.segmentation_model)
    emb = Path(config.embedding_model)
    return seg.is_file() and emb.is_file()


def _validate_sherpa_models(config: DiarizationConfig) -> tuple[Path, Path]:
    """Validate that required sherpa-onnx model paths are configured and exist.

    Raises:
        ValueError: If segmentation_model or embedding_model is not configured.
        FileNotFoundError: If configured model paths do not exist on disk.
    """
    missing_configs: list[str] = []
    if not config.segmentation_model or str(config.segmentation_model).strip() == "":
        missing_configs.append("segmentation_model")
    if not config.embedding_model or str(config.embedding_model).strip() == "":
        missing_configs.append("embedding_model")

    if missing_configs:
        raise ValueError(
            "sherpa-onnx backend requires model paths to be configured. "
            f"Missing configuration: {', '.join(missing_configs)}. "
            "Please provide --segmentation-model and --embedding-model."
        )

    seg_path = Path(config.segmentation_model)
    emb_path = Path(config.embedding_model)
    missing_files: list[str] = []

    if not seg_path.exists() or seg_path.is_dir():
        missing_files.append(f"segmentation model file not found: {seg_path}")
    if not emb_path.exists() or emb_path.is_dir():
        missing_files.append(f"embedding model file not found: {emb_path}")

    if missing_files:
        raise FileNotFoundError(
            "sherpa-onnx model file(s) missing: " + "; ".join(missing_files)
        )

    return seg_path, emb_path


def _diarize_sherpa(
    audio_path: Path,
    duration_ms: int,
    config: DiarizationConfig,
) -> DiarizationResult:
    """Run diarization using sherpa-onnx OfflineSpeakerDiarization."""
    # First load audio to validate path and content
    audio = load_audio(audio_path, config.sample_rate)
    if len(audio) == 0:
        return DiarizationResult(
            turns=[], num_speakers_detected=0, method="sherpa_empty",
            warnings=["Audio is empty"],
        )

    sherpa = sherpa_onnx
    if sherpa is None:
        raise RuntimeError(
            "sherpa-onnx is required for the sherpa backend. "
            "Install with: pip install sherpa-onnx"
        )

    seg_path, emb_path = _validate_sherpa_models(config)

    warnings: list[str] = []

    # Build sherpa config
    sd_config = sherpa.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(seg_path),
            ),
            num_threads=2,
        ),
        embedding=sherpa.SpeakerEmbeddingExtractorConfig(
            model=str(emb_path),
            num_threads=2,
        ),
        clustering=sherpa.FastClusteringConfig(
            num_clusters=config.num_speakers if config.num_speakers else -1,
            threshold=0.5,
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )

    try:
        sd = sherpa.OfflineSpeakerDiarization(sd_config)
    except Exception as exc:
        raise RuntimeError(
            f"sherpa-onnx diarization initialization failed: {exc}. "
            f"Ensure sherpa-onnx models are valid."
        ) from exc

    # Resample audio if sherpa expects different sample rate
    if hasattr(sd, "sample_rate") and sd.sample_rate != config.sample_rate:
        audio = load_audio(audio_path, sd.sample_rate)

    # Run diarization
    processed = sd.process(audio)
    segments = processed.sort_by_start_time() if hasattr(processed, "sort_by_start_time") else processed

    # Convert to SpeakerTurn
    turns: list[SpeakerTurn] = []
    speaker_set: set[str] = set()
    for seg in segments:
        start_ms = int(seg.start * 1000)
        end_ms = int(seg.end * 1000)
        speaker_id = f"SPEAKER_{seg.speaker:02d}"
        speaker_set.add(speaker_id)

        start_ms = max(0, start_ms)
        end_ms = min(duration_ms, end_ms)
        if end_ms > start_ms:
            turns.append(SpeakerTurn(start_ms, end_ms, speaker_id))

    turns = _merge_consecutive_turns(turns)

    return DiarizationResult(
        turns=_validate_turns(turns, duration_ms),
        num_speakers_detected=len(speaker_set),
        method="sherpa_onnx",
        warnings=warnings,
        metadata={
            "backend": "sherpa-onnx",
            "segmentation_model": str(seg_path),
            "embedding_model": str(emb_path),
            "raw_segments": len(segments),
        },
    )


# ---------------------------------------------------------------------------
# resemblyzer backend
# ---------------------------------------------------------------------------

def _diarize_resemblyzer(
    audio_path: Path,
    duration_ms: int,
    config: DiarizationConfig,
) -> DiarizationResult:
    """Run diarization using resemblyzer embeddings + sklearn clustering."""
    # First load audio to validate path and content
    audio = load_audio(audio_path, config.sample_rate)

    warnings: list[str] = []
    if len(audio) == 0:
        return DiarizationResult(
            turns=[], num_speakers_detected=0, method="resemblyzer_empty",
            warnings=["Audio is empty"],
        )

    # Run VAD
    speech_segments = _energy_vad(audio, config.sample_rate, min_speech_ms=config.min_segment_ms)
    if not speech_segments:
        return DiarizationResult(
            turns=[], num_speakers_detected=0, method="resemblyzer_no_speech",
            warnings=["No speech detected by energy VAD"],
        )

    logger.info("VAD found %d speech segments", len(speech_segments))

    # Filter segments too short for embedding
    usable = [(s, e) for s, e in speech_segments if (e - s) >= config.min_utterance_ms]
    if not usable:
        warnings.append("All segments shorter than min_utterance_ms; single-speaker fallback")
        turns = [
            SpeakerTurn(s, min(e, duration_ms), "SPEAKER_00")
            for s, e in speech_segments if min(e, duration_ms) > s
        ]
        return DiarizationResult(
            turns=_validate_turns(turns, duration_ms),
            num_speakers_detected=1, method="resemblyzer_single_fallback",
            warnings=warnings,
        )

    enc_cls = VoiceEncoder
    if enc_cls is None:
        raise RuntimeError("resemblyzer is required but not installed")

    encoder = enc_cls("cpu")
    embeddings: list[np.ndarray] = []
    segment_indices: list[int] = []

    for idx, (start_ms, end_ms) in enumerate(usable):
        start_sample = int(start_ms * config.sample_rate / 1000)
        end_sample = int(end_ms * config.sample_rate / 1000)
        segment_audio = audio[start_sample:end_sample]

        if len(segment_audio) < int(config.min_utterance_ms * config.sample_rate / 1000):
            continue
        try:
            emb = encoder.embed_utterance(segment_audio, rate=config.embedding_rate)
            embeddings.append(emb)
            segment_indices.append(idx)
        except Exception as exc:
            logger.warning("Embedding failed for segment %d: %s", idx, exc)

    if len(embeddings) < 2:
        warnings.append("Fewer than 2 embeddable segments; single-speaker fallback")
        turns = [
            SpeakerTurn(s, min(e, duration_ms), "SPEAKER_00")
            for s, e in speech_segments if min(e, duration_ms) > s
        ]
        return DiarizationResult(
            turns=_validate_turns(turns, duration_ms),
            num_speakers_detected=1, method="resemblyzer_single_fallback",
            warnings=warnings,
        )

    emb_matrix = np.stack(embeddings)

    # Cluster
    num_speakers = config.num_speakers
    method = "resemblyzer_spectral"

    if num_speakers is not None:
        if num_speakers < 1 or num_speakers > config.max_speakers:
            raise ValueError(f"num_speakers must be 1..{config.max_speakers}, got {num_speakers}")
        if num_speakers == 1:
            labels = np.zeros(len(embeddings), dtype=int)
        elif num_speakers <= len(embeddings):
            labels = _cluster_embeddings(emb_matrix, num_speakers, warnings)
        else:
            warnings.append(f"Requested {num_speakers} speakers but only {len(embeddings)} segments")
            labels = np.arange(len(embeddings))
    else:
        labels, num_speakers = _auto_detect_speakers(emb_matrix, config, warnings)
        method = "resemblyzer_auto"

    # Build turns
    speaker_labels = {label: f"SPEAKER_{label:02d}" for label in sorted(set(labels))}
    labeled_map: dict[int, str] = {}
    for emb_idx, label in enumerate(labels):
        labeled_map[segment_indices[emb_idx]] = speaker_labels[label]

    turns: list[SpeakerTurn] = []
    for seg_idx, (start_ms, end_ms) in enumerate(speech_segments):
        matched_usable = [
            i for i, (s, e) in enumerate(usable) if s == start_ms and e == end_ms
        ]
        if matched_usable and matched_usable[0] in labeled_map:
            speaker_id = labeled_map[matched_usable[0]]
        else:
            speaker_id = _nearest_speaker(start_ms, end_ms, usable, labeled_map)

        clamped_end = min(end_ms, duration_ms)
        if clamped_end > start_ms:
            turns.append(SpeakerTurn(start_ms, clamped_end, speaker_id))

    turns = _merge_consecutive_turns(turns)

    return DiarizationResult(
        turns=_validate_turns(turns, duration_ms),
        num_speakers_detected=num_speakers if isinstance(num_speakers, int) else len(set(labels)),
        method=method, warnings=warnings,
        metadata={
            "backend": "resemblyzer",
            "total_speech_segments": len(speech_segments),
            "embedded_segments": len(embeddings),
        },
    )


def _cluster_embeddings(
    embeddings: np.ndarray,
    num_speakers: int,
    warnings: list[str],
) -> np.ndarray:
    """Cluster embeddings into num_speakers groups."""
    sc_cls = SpectralClustering
    if sc_cls is None and AgglomerativeClustering is None:
        raise RuntimeError("scikit-learn is required for clustering")

    if num_speakers >= len(embeddings):
        return np.arange(len(embeddings))

    # Cosine similarity -> affinity matrix
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    normalized = embeddings / norms
    similarity = normalized @ normalized.T
    affinity = (similarity + 1) / 2  # shift to [0, 1]

    if sc_cls is not None:
        try:
            clustering = sc_cls(
                n_clusters=num_speakers, affinity="precomputed",
                random_state=42, n_init=10,
            )
            return clustering.fit_predict(affinity)
        except Exception as exc:
            warnings.append(f"Spectral clustering failed ({exc}); falling back to agglomerative")

    ac_cls = AgglomerativeClustering
    if ac_cls is not None:
        clustering = ac_cls(
            n_clusters=num_speakers, metric="cosine", linkage="average",
        )
        return clustering.fit_predict(embeddings)

    raise RuntimeError("No clustering method available")


def _auto_detect_speakers(
    embeddings: np.ndarray,
    config: DiarizationConfig,
    warnings: list[str],
) -> tuple[np.ndarray, int]:
    """Try multiple speaker counts; pick best silhouette score."""
    n = len(embeddings)
    max_k = min(config.max_speakers, n - 1)
    if max_k < 2:
        return np.zeros(n, dtype=int), 1

    try:
        from sklearn.metrics import silhouette_score  # type: ignore[import-untyped]
    except ImportError:
        warnings.append("scikit-learn silhouette_score unavailable; defaulting to 2 speakers")
        k = min(2, len(embeddings))
        return _cluster_embeddings(embeddings, k, warnings), k

    best_score = -1.0
    best_labels = np.zeros(n, dtype=int)
    best_k = 1

    for k in range(2, max_k + 1):
        try:
            labels = _cluster_embeddings(embeddings, k, warnings)
            if len(set(labels)) < 2:
                continue
            score = silhouette_score(embeddings, labels, metric="cosine")
            if score > best_score:
                best_score = score
                best_labels = labels
                best_k = k
        except Exception:
            continue

    if best_score < config.confidence_threshold:
        warnings.append(
            f"Best silhouette ({best_score:.3f}) below threshold "
            f"({config.confidence_threshold}); results may be unreliable"
        )
    return best_labels, best_k


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nearest_speaker(
    start_ms: int, end_ms: int,
    usable_segments: list[tuple[int, int]],
    labeled_map: dict[int, str],
) -> str:
    """Find the nearest labeled segment and inherit its speaker ID."""
    mid = (start_ms + end_ms) // 2
    best_dist = float("inf")
    best_speaker = "SPEAKER_UNKNOWN"
    for idx, (s, e) in enumerate(usable_segments):
        if idx not in labeled_map:
            continue
        dist = abs(mid - (s + e) // 2)
        if dist < best_dist:
            best_dist = dist
            best_speaker = labeled_map[idx]
    return best_speaker


def _merge_consecutive_turns(turns: list[SpeakerTurn]) -> list[SpeakerTurn]:
    """Merge adjacent turns with the same speaker and small gap."""
    if not turns:
        return turns
    merged: list[SpeakerTurn] = [turns[0]]
    for turn in turns[1:]:
        prev = merged[-1]
        if turn.speaker_id == prev.speaker_id and turn.start_ms - prev.end_ms <= 500:
            merged[-1] = SpeakerTurn(prev.start_ms, turn.end_ms, prev.speaker_id)
        else:
            merged.append(turn)
    return merged


def _validate_turns(turns: list[SpeakerTurn], duration_ms: int) -> list[SpeakerTurn]:
    """Clamp turns to valid range and remove degenerate ones."""
    valid: list[SpeakerTurn] = []
    for t in turns:
        start = max(0, t.start_ms)
        end = min(duration_ms, t.end_ms)
        if end > start:
            try:
                turn = SpeakerTurn(start, end, t.speaker_id)
                turn.validate(duration_ms)
                valid.append(turn)
            except (ValueError, TypeError):
                continue
    return sorted(valid, key=lambda x: (x.start_ms, x.end_ms))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def diarize_audio(
    audio_path: Path,
    duration_ms: int,
    config: DiarizationConfig | None = None,
) -> DiarizationResult:
    """Run speaker diarization on an audio file.

    Parameters:
        audio_path: Path to a 16 kHz mono WAV file.
        duration_ms: Total duration in ms (for validation).
        config: Tunable parameters.

    Returns:
        DiarizationResult with anonymous speaker turns.

    Raises:
        RuntimeError: If no backend is available.
        FileNotFoundError: If the audio file doesn't exist.
    """
    if np is None:
        raise RuntimeError(f"Diarization backend unavailable: {BACKEND_UNAVAILABLE_REASON}")
    if config is None:
        config = DiarizationConfig()

    backend = config.backend
    if backend not in ("auto", "sherpa", "resemblyzer"):
        raise ValueError(f"Unknown backend: {backend!r}. Use 'auto', 'sherpa', or 'resemblyzer'.")

    # Select backend
    if backend == "auto":
        sherpa_installed = _SHERPA_AVAILABLE or sherpa_onnx is not None
        sherpa_ready = sherpa_installed and _has_valid_sherpa_models(config)
        resemblyzer_ready = (_RESEMBLYZER_AVAILABLE or VoiceEncoder is not None) and (
            _SKLEARN_AVAILABLE or SpectralClustering is not None
        )

        if sherpa_ready:
            backend = "sherpa"
        elif resemblyzer_ready:
            backend = "resemblyzer"
        elif sherpa_installed:
            raise RuntimeError(
                "No usable diarization backend: sherpa-onnx is installed, but required "
                "model files were not configured or do not exist on disk. "
                "Please configure segmentation_model and embedding_model paths "
                "(or --segmentation-model and --embedding-model in the CLI), "
                "or install the resemblyzer fallback: pip install 'chomp-chomp[diarize]'"
            )
        else:
            raise RuntimeError(
                f"Diarization backend unavailable: {BACKEND_UNAVAILABLE_REASON}"
            )

    if backend == "sherpa":
        if not _SHERPA_AVAILABLE and sherpa_onnx is None:
            raise RuntimeError("sherpa-onnx backend requested but not installed")
        return _diarize_sherpa(audio_path, duration_ms, config)
    elif backend == "resemblyzer":
        if (not _RESEMBLYZER_AVAILABLE and VoiceEncoder is None) or (not _SKLEARN_AVAILABLE and SpectralClustering is None):
            raise RuntimeError("resemblyzer backend requested but dependencies missing")
        return _diarize_resemblyzer(audio_path, duration_ms, config)
    else:
        raise ValueError(f"Unknown backend: {backend!r}")
