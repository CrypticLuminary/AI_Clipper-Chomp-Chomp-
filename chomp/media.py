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
# Preserve the elapsed time before delayed audio packets when writing timestamp-free PCM.
SOURCE_CLOCK_AUDIO_FILTER = "aresample=async=1:first_pts=0"


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


def scan_audio(path: Path, *, audio_stream: int = 0) -> list[float]:
    """Stream one-second audio levels on the source clock from the selected track."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(path),
        "-map", f"0:a:{audio_stream}", "-af", SOURCE_CLOCK_AUDIO_FILTER,
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1",
    ]
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


def resolve_layout(layout: str, facecam_roi: tuple[int, int, int, int] | None) -> str:
    """Select one of the two primary clip formats without pretending to detect a facecam."""
    if layout not in {"auto", "facecam-gameplay", "gameplay-only", "fit", "letterbox"}:
        raise ValueError("Unknown layout. Use auto, facecam-gameplay, gameplay-only, fit, or letterbox")
    choice = ("facecam-gameplay" if facecam_roi else "gameplay-only") if layout == "auto" else layout
    if choice == "facecam-gameplay" and facecam_roi is None:
        raise ValueError("facecam-gameplay requires --facecam-roi x,y,w,h; automatic facecam detection is not implemented")
    return choice


def _validate_roi(roi: tuple[int, int, int, int] | None, vw: int, vh: int, name: str) -> tuple[int, int, int, int] | None:
    if roi is None:
        return None
    if len(roi) != 4 or any(type(value) is not int for value in roi):
        raise ValueError(f"{name} must be a rectangle of four integers")
    x, y, w, h = roi
    if min(x, y) < 0 or min(w, h) <= 0 or x+w > vw or y+h > vh:
        raise ValueError(f"{name} must fit within the source video ({vw}x{vh})")
    return roi


def render_preview(
    source: Path, output: Path, start_ms: int, end_ms: int,
    width: int = 540, height: int = 960, *,
    captions_path: Path | None = None,
    facecam_roi: tuple[int, int, int, int] | None = None,
    gameplay_roi: tuple[int, int, int, int] | None = None,
    layout: str = "auto", audio_stream: int = 0, fit_facecam: bool = False,
) -> None:
    """Render exact selected range into one of two primary vertical layouts.

    facecam-gameplay: upper 35% confirmed facecam ROI, lower 65% gameplay.
    gameplay-only: fill portrait with centered gameplay crop (not a letterbox strip).
    fit: fit the full recording against a blurred background.
    letterbox: legacy diagnostic mode, preserving full landscape video.

    There is no automatic facecam detector or intelligent action tracking yet.
    Cropping can omit HUD/edge action; check previews before publishing.
    """
    from .models import checked_span
    meta = probe(source)
    checked_span(start_ms, end_ms, meta["duration_ms"])
    video = next((stream for stream in meta["streams"] if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("The source has no video stream to render")
    if min(width, height) < 2 or width % 2 or height % 2:
        raise ValueError("Output width/height must be positive even numbers")
    if type(audio_stream) is not int or audio_stream < 0:
        raise ValueError("audio_stream must be a nonnegative integer")
    audio_tracks = [s for s in meta["streams"] if s.get("codec_type") == "audio"]
    if audio_stream >= len(audio_tracks):
        raise ValueError(f"Audio track {audio_stream} unavailable; video has {len(audio_tracks)} audio track(s)")
    vw, vh = int(video["width"]), int(video["height"])
    facecam_roi = _validate_roi(facecam_roi, vw, vh, "Facecam ROI")
    gameplay_roi = _validate_roi(gameplay_roi, vw, vh, "Gameplay ROI")
    chosen_layout = resolve_layout(layout, facecam_roi)

    # Probe source and build filters *before* starting the FFmpeg subprocess.
    # The same output timestamp source must apply to video, audio and captions.
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = (end_ms - start_ms) / 1000
    base = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error",
            "-ss", str(start_ms / 1000), "-i", str(source), "-t", str(duration)]

    def crop_filter(roi: tuple[int, int, int, int] | None) -> str:
        if roi is None:
            return ""
        x, y, w, h = roi
        return f"crop={w}:{h}:{x}:{y},"

    if chosen_layout == "facecam-gameplay":
        assert facecam_roi is not None
        x, y, w, h = facecam_roi
        top = round(height * .35 / 2) * 2
        bottom = height - top
        if fit_facecam:
            camera = (f"scale={width}:{top}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                      f"pad={width}:{top}:(ow-iw)/2:(oh-ih)/2")
        else:
            camera = f"scale={width}:{top}:force_original_aspect_ratio=increase,crop={width}:{top}"
        vf = (f"[0:v]split=2[cam][game];"
              f"[cam]crop={w}:{h}:{x}:{y},{camera},setsar=1[camout];"
              f"[game]{crop_filter(gameplay_roi)}scale={width}:{bottom}:force_original_aspect_ratio=increase,"
              f"crop={width}:{bottom},setsar=1[gameout];"
              f"[camout][gameout]vstack=inputs=2,format=yuv420p[stage]")
    elif chosen_layout == "gameplay-only":
        vf = (f"[0:v]{crop_filter(gameplay_roi)}scale={width}:{height}:force_original_aspect_ratio=increase,"
              f"crop={width}:{height},setsar=1,format=yuv420p[stage]")
    elif chosen_layout == "fit":
        # Blur at lower resolution before filling the portrait canvas.
        blur_w, blur_h = max(2, width // 6 * 2), max(2, height // 6 * 2)
        vf = (
            f"[0:v]{crop_filter(gameplay_roi)}split=2[foreground][background];"
            f"[foreground]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1[fit];"
            f"[background]scale={blur_w}:{blur_h}:force_original_aspect_ratio=increase,"
            f"crop={blur_w}:{blur_h},gblur=sigma=10,scale={width}:{height},setsar=1[blur];"
            "[blur][fit]overlay=(W-w)/2:(H-h)/2,format=yuv420p[stage]"
        )
    else:  # explicit legacy letterbox, for diagnostics/comparison
        vf = (f"[0:v]{crop_filter(gameplay_roi)}scale={width}:{height}:force_original_aspect_ratio=decrease,"
              f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[stage]")
    if captions_path is not None and captions_path.is_file() and captions_path.stat().st_size:
        if (captions_path.parent.resolve() != output.parent.resolve()
            or not captions_path.name.startswith(("candidate_", "manual_"))
            or captions_path.suffix.lower() != ".srt"):
            raise ValueError("Generated SRT must be beside output, named candidate_*.srt or manual_*.srt")
        vf += (f";[stage]subtitles=filename={captions_path.name}:"
               f"force_style='FontName=Arial,FontSize=10,Alignment=2,MarginV=24,Outline=1,Shadow=0'[out]")
    else:
        vf += ";[stage]null[out]"

    # -map 0:a:N specifies the Nth AUDIO stream, not the Nth overall stream.
    base += ["-filter_complex", vf, "-map", "[out]", "-map", f"0:a:{audio_stream}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
             "-disposition:a:0", "default", "-movflags", "+faststart", str(output)]
    try:
        subprocess.run(base, text=True, capture_output=True, check=True, cwd=str(output.parent))
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Rendering failed: {exc.stderr[-1000:]}") from exc
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Render produced no media")
    rendered = probe(output)
    outvideo = next((s for s in rendered["streams"] if s.get("codec_type") == "video"), {})
    if (outvideo.get("codec_name"), outvideo.get("width"), outvideo.get("height")) != ("h264", width, height):
        raise RuntimeError("Rendered video did not pass H.264 portrait verification")
    if not any(s.get("codec_type") == "audio" and s.get("codec_name") == "aac" for s in rendered["streams"]):
        raise RuntimeError("Rendered clip is missing AAC audio")
    if abs(rendered["duration_ms"] - (end_ms - start_ms)) > 500:
        raise RuntimeError("Rendered clip has unexpected duration; verify source timestamps")
