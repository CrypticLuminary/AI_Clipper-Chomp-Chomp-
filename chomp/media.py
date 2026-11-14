"""FFmpeg audio scan and human-review preview render; no remote acquisition."""
from __future__ import annotations

from array import array
import json
import math
import re
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


def exported_audio_level(path: Path) -> float:
    """Decode the *rendered* audio and report mean dBFS.

    A stream can exist but be silent: checking only AAC codec presence is not
    sufficient. -inf means complete digital silence. This is a diagnostic,
    not a reason to drop intentional quiet gameplay footage.
    """
    result = exec_checked(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path),
                           "-map", "0:a:0", "-vn", "-af", "volumedetect",
                           "-f", "null", "-"])
    match = re.search(r"mean_volume:\s*(-?inf|-?\d+(?:\.\d+)?) dB", result.stderr)
    if match is None:
        raise RuntimeError("Unable to measure decoded export audio level")
    return float(match.group(1))


def render_preview(source: Path, output: Path, start_ms: int, end_ms: int, width: int = 540, height: int = 960, *, captions_path: Path | None = None, facecam_roi: tuple[int, int, int, int] | None = None) -> None:
    """Produce a vertical review export with optional sidecar-SRT burn-in.

    facecam_roi is x,y,w,h in *original source pixels*. Omit for a full-
    frame letterbox preserving gameplay. No automatic facecam detection.
    """
    from .models import checked_span
    meta = probe(source)
    checked_span(start_ms, end_ms, meta["duration_ms"])
    video = next((stream for stream in meta["streams"] if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("The source has no video stream to render")
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = (end_ms - start_ms) / 1000
    base = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error", "-ss", str(start_ms/1000), "-i", str(source), "-t", str(duration)]
    if facecam_roi is not None:
        x,y,w,h = facecam_roi
        vw,vh = int(video["width"]),int(video["height"])
        if any(type(v) is not int for v in facecam_roi) or min(x,y)<0 or w<=0 or h<=0 or x+w>vw or y+h>vh:
            raise ValueError("Facecam ROI must be within source video pixel dimensions")
        top = round(height/3)
        bottom = height-top
        vf = (f"[0:v]split=2[face][game];"
              f"[face]crop={w}:{h}:{x}:{y},scale={width}:{top}:force_original_aspect_ratio=increase,crop={width}:{top},setsar=1[faceout];"
              f"[game]scale={width}:{bottom}:force_original_aspect_ratio=decrease,pad={width}:{bottom}:(ow-iw)/2:(oh-ih)/2,setsar=1[gameout];"
              f"[faceout][gameout]vstack=inputs=2,format=yuv420p[stage]")
    else:
        vf = f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[stage]"
    if captions_path is not None and captions_path.is_file() and captions_path.stat().st_size:
        # Use a simple generated filename relative to cwd: avoids breaking the
        # FFmpeg filter syntax with drive letters, quotes or source path chars.
        if captions_path.parent.resolve() != output.parent.resolve() or not captions_path.name.startswith("candidate_") or captions_path.suffix.lower() != ".srt":
            raise ValueError("Generated SRT must be beside the output with a candidate_ prefix")
        vf += f";[stage]subtitles=filename={captions_path.name}:force_style='FontName=Arial,FontSize=10,Alignment=2,MarginV=24,Outline=1,Shadow=0'[out]"
        vmap = "[out]"
    else:
        vf += ";[stage]null[out]"
        vmap = "[out]"
    base += ["-filter_complex", vf, "-map", vmap, "-map", "0:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-disposition:a:0", "default", "-movflags", "+faststart", str(output)]
    try:
        result = subprocess.run(base, text=True, capture_output=True, check=True, cwd=str(output.parent))
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Rendering failed: {exc.stderr[-1000:]}") from exc
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Render produced no media")
    rendered = probe(output)
    outvideo = next((s for s in rendered["streams"] if s.get("codec_type") == "video"), {})
    if (outvideo.get("codec_name"),outvideo.get("width"),outvideo.get("height")) != ("h264",width,height):
        raise RuntimeError("Rendered video did not pass H.264 portrait verification")
    if not any(s.get("codec_type")=="audio" and s.get("codec_name")=="aac" for s in rendered["streams"]):
        raise RuntimeError("Rendered clip is missing AAC audio")
