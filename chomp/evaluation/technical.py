"""Decoded audio and output-clock checks; never infer human quality."""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import sys

from ..media import decode_audio_window, exec_checked, probe, rms_dbfs


def _samples(raw):
    result = array("h")
    result.frombytes(raw)
    if sys.byteorder != "little":
        result.byteswap()
    return result


def inspect_export(source: Path, preview: Path, *, start_ms: int, end_ms: int, audio_stream: int) -> dict:
    duration = end_ms - start_ms
    meta = probe(preview)
    video = next(s for s in meta["streams"] if s["codec_type"] == "video")
    audio = next(s for s in meta["streams"] if s["codec_type"] == "audio")
    # Keep waveform verification bounded, using the same source interval/track.
    length = min(duration, 600_000)
    original = decode_audio_window(source, start_ms, start_ms + length, audio_stream=audio_stream)
    exported = decode_audio_window(preview, 0, length)
    a, b = _samples(original), _samples(exported)
    source_level, output_level = rms_dbfs(original), rms_dbfs(exported)
    bin_size = 320  # 20 ms at 16 kHz.
    envelope_a = [rms_dbfs(original[i:i + bin_size * 2]) for i in range(0, len(original), bin_size * 2)]
    envelope_b = [rms_dbfs(exported[i:i + bin_size * 2]) for i in range(0, len(exported), bin_size * 2)]
    # Only give a lag when there is enough temporal variation to identify one.
    lag, correlation = None, None
    if envelope_a and max(envelope_a) - min(envelope_a) > 12 and max(envelope_b) - min(envelope_b) > 12:
        scores = []
        for shift in range(-5, 6):
            pairs = [(value, envelope_b[i + shift]) for i, value in enumerate(envelope_a)
                     if 0 <= i + shift < len(envelope_b)]
            if len(pairs) < 10:
                continue
            mean_a = sum(x for x, y in pairs) / len(pairs)
            mean_b = sum(y for x, y in pairs) / len(pairs)
            numerator = sum((x - mean_a) * (y - mean_b) for x, y in pairs)
            denominator = math.sqrt(sum((x - mean_a) ** 2 for x, y in pairs) * sum((y - mean_b) ** 2 for x, y in pairs))
            if denominator:
                scores.append((numerator / denominator, shift))
        if scores:
            correlation, shift = max(scores, key=lambda pair: (pair[0], -abs(pair[1])))
            if correlation >= .8:
                lag = shift * 20
    # Sample-aligned correlation is separate from the envelope lag; it detects
    # wrong tones/tracks without pretending speech has been lip-sync reviewed.
    indices = range(0, min(len(a), len(b)), max(1, min(len(a), len(b)) // 8000))
    numerator = sum(a[i] * b[i] for i in indices)
    denominator = math.sqrt(sum(a[i] ** 2 for i in indices) * sum(b[i] ** 2 for i in indices))
    waveform = numerator / denominator if denominator else None
    starts = [float(s.get("start_time", 0)) * 1000 for s in (video, audio)]
    exec_checked(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror",
                  "-i", str(preview), "-f", "null", "-"], timeout=max(60, duration / 1000 * 5))
    return {"width": video["width"], "height": video["height"], "duration_ms": meta["duration_ms"],
            "full_decode_passed": True, "source_audio_stream": audio_stream,
            "measured_audio_ms": length, "source_dbfs": source_level, "output_dbfs": output_level,
            "missing_audio": False,
            "expected_source_silence": source_level <= -60, "silent_audio": output_level <= -60,
            "unexpected_silent_audio": source_level > -60 and output_level <= -60,
            "waveform_correlation": waveform, "envelope_correlation": correlation,
            "envelope_lag_ms": lag, "stream_start_delta_ms": starts[1] - starts[0],
            "sync_note": "Envelope lag has 20ms resolution; null for insufficient temporal evidence. Human lip-sync is unmeasured."}
