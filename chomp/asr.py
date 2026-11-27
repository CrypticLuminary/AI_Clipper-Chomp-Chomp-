"""Bounded local transcription on the same selected-track clock as FFmpeg."""
from __future__ import annotations

from functools import cached_property
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .artifacts import artifact_key, artifact_lock, digest_file, write_json
from .media import decode_audio_window
from .signals import read_transcript

_CHUNK_MS = 8 * 60 * 1000
_OVERLAP_MS = 2000
_SCHEMA = "source-clock-asr/2"


class LocalTranscriber:
    def __init__(self, model: str = "small", device: str = "cpu", language: str | None = None,
                 *, quiet_gain: bool = False):
        self.model = model
        self.device = device
        self.language = language
        self.quiet_gain = quiet_gain
        self.last_cache_hit = False

    @cached_property
    def model_path(self) -> Path:
        try:
            from faster_whisper.utils import download_model
        except ImportError as exc:
            raise RuntimeError("Install chomp-chomp[asr] or provide --transcript") from exc
        if Path(self.model).is_dir():
            return Path(self.model).resolve()
        try:
            return Path(download_model(self.model, local_files_only=True)).resolve()
        except (ValueError, OSError) as exc:
            raise RuntimeError(
                f"Local Whisper model {self.model!r} is unavailable. Supply a cached model or --transcript."
            ) from exc

    @cached_property
    def fingerprint(self) -> dict:
        versions = {}
        for name in ("faster-whisper", "ctranslate2"):
            try:
                versions[name] = version(name)
            except PackageNotFoundError:
                versions[name] = None
        files = {}
        for name in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json",
                     "vocabulary.json", "vocabulary.txt"):
            path = self.model_path / name
            if path.is_file():
                files[name] = digest_file(path)
        return {"model": self.model, "weights": files, "device": self.device,
                "language": self.language, "versions": versions, "schema": _SCHEMA,
                **({"gain": "quiet-local/1"} if self.quiet_gain else {})}

    @cached_property
    def engine(self):
        from faster_whisper import WhisperModel

        local_path = self.model_path
        if not (local_path / "tokenizer.json").is_file():
            raise RuntimeError("Local Whisper model directory must include tokenizer.json")
        return WhisperModel(str(local_path), device=self.device, local_files_only=True,
                            compute_type="int8" if self.device == "cpu" else "float16")

    def transcribe(self, media: Path, start_ms: int, end_ms: int, audio_stream: int, *, words: bool) -> list[dict]:
        import numpy as np

        rows = []
        for core_start in range(start_ms, end_ms, _CHUNK_MS):
            core_end = min(end_ms, core_start + _CHUNK_MS)
            begin = max(start_ms, core_start - _OVERLAP_MS)
            finish = min(end_ms, core_end + _OVERLAP_MS)
            raw = decode_audio_window(media, begin, finish, audio_stream=audio_stream)
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if not samples.size or not np.any(np.abs(samples) > .0001):
                continue
            if self.quiet_gain:
                # Boost quiet PCM for recognition while keeping the original recording and its clock.
                size = 16_000
                prior_gain = 1.0
                for start in range(0, samples.size, size):
                    block = samples[start:start + size]
                    peak = float(np.max(np.abs(block)))
                    gain = min(8.0, max(1.0, .18 / max(peak, .003))) if peak >= .003 else 1.0
                    ramp = min(400, block.size)
                    # Limit the ramp at a sudden transient following a quiet block.
                    block[:ramp] *= np.linspace(min(prior_gain, gain), gain, ramp, dtype=np.float32)
                    block[ramp:] *= gain
                    prior_gain = gain
            segments, _ = self.engine.transcribe(samples, vad_filter=True, beam_size=3,
                                                 language=self.language, word_timestamps=words,
                                                 condition_on_previous_text=False)
            for segment in segments:
                left = max(begin, begin + round(segment.start * 1000))
                right = min(finish, begin + round(segment.end * 1000))
                midpoint = (left + right) // 2
                if right <= left or not core_start <= midpoint < core_end or not segment.text.strip():
                    continue
                row = {"start_ms": left, "end_ms": right, "text": segment.text.strip()}
                if words and segment.words:
                    timed_words = []
                    for token in segment.words:
                        text = token.word.strip()
                        if not text or token.start is None or token.end is None:
                            continue
                        word_start = max(left, begin + round(token.start * 1000))
                        word_end = min(right, begin + round(token.end * 1000))
                        if word_end > word_start:
                            timed_words.append({"start_ms": word_start, "end_ms": word_end,
                                                "text": text, "confidence": token.probability})
                    if timed_words and "".join(w["text"] for w in timed_words).replace(" ", "") == row["text"].replace(" ", ""):
                        row["words"] = timed_words
                if rows and row["text"] == rows[-1]["text"] and left < rows[-1]["end_ms"]:
                    continue
                rows.append(row)
        return rows

    def cached(self, cache: Path, media: Path, source_id: str, duration_ms: int, audio_stream: int,
               *, start_ms: int = 0, end_ms: int | None = None, words: bool = False):
        end_ms = duration_ms if end_ms is None else end_ms
        provenance = {"source_id": source_id, "audio_stream": audio_stream, "range_ms": [start_ms, end_ms],
                      "word_timestamps": words, "asr": self.fingerprint}
        key = artifact_key(provenance)
        path = cache / "transcripts" / (key + ".json")
        with artifact_lock(cache / "locks" / (key + ".lock"), timeout=600):
            self.last_cache_hit = path.is_file()
            if not self.last_cache_hit:
                segments = self.transcribe(media, start_ms, end_ms, audio_stream, words=words)
                write_json(path, {"provenance": provenance, "segments": segments})
            return read_transcript(path, duration_ms), path
