"""FFmpeg audio scan and human-review preview render; no remote acquisition."""
from __future__ import annotations

from array import array
import json
import math
from pathlib import Path
import subprocess
import sys

SAMPLE_RATE = 2000  # low-cost detector, not ASR quality


def exec_checked(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, text=True, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing executable: {args[0]} (install FFmpeg and add it to PATH)") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Command failed: {args[0]}: {exc.stderr[-1000:]}") from exc


def probe(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    result = exec_checked(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-show_streams", "-of", "json", str(path)])
    info = json.loads(result.stdout)
    try:
        duration_ms = round(float(info["format"]["duration"]) * 1000)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Source has no known duration") from exc
    if duration_ms <= 0 or not math.isfinite(duration_ms):
        raise ValueError("Source duration must be positive and finite")
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise ValueError("Source must contain an audio stream for the VOD baseline")
    return {"duration_ms": duration_ms, "streams": info.get("streams", [])}


def rms_dbfs(data: bytes) -> float:
    samples = array("h")
    samples.frombytes(data[:len(data)//2*2])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return -90.0
    power = sum(int(s) * int(s) for s in samples) / len(samples)
    if power <= 0:
        return -90.0
    return max(-90., min(0., 10 * math.log10(power / (32768**2))))


def scan_audio(path: Path) -> list[float]:
    """Stream downsampled PCM without writing 4-hour uncompressed audio to disk."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required") from exc
    curve: list[float] = []
    assert proc.stdout is not None and proc.stderr is not None
    while True:
        chunk = proc.stdout.read(SAMPLE_RATE * 2)
        if not chunk:
            break
        curve.append(rms_dbfs(chunk))
    proc.stdout.close()
    error = proc.stderr.read().decode("utf-8", errors="replace")
    proc.stderr.close()
    if proc.wait() != 0:
        raise RuntimeError(f"Audio scan failed: {error[-1000:]}")
    return curve


def render_preview(source: Path, output: Path, start_ms: int, end_ms: int, width: int = 540, height: int = 960) -> None:
    """Letterbox, never hide gameplay or invent a facecam. Preview requires review."""
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = (end_ms - start_ms) / 1000
    vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
    exec_checked(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error", "-ss", str(start_ms/1000), "-i", str(source), "-t", str(duration), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output)])
    # Fail loudly on invalid output. Codec verification also available through the review manifest.
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Render produced no media")
