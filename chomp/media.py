"""Selected-track audio and validated FFmpeg portrait exports."""
from __future__ import annotations

from array import array
from dataclasses import dataclass
from fractions import Fraction
import json
import math
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

from .artifacts import digest_file, staged_file
from .models import checked_span
from .sound import SoundCue, cues_in_range

SAMPLE_RATE = 2000
SOURCE_CLOCK_AUDIO_FILTER = "aresample=async=1:first_pts=0"
RENDER_VERSION = 7
LAYOUTS = {"facecam-gameplay", "gameplay-only", "fit", "letterbox"}
Rectangle = tuple[int, int, int, int]


@dataclass(frozen=True)
class LayoutSegment:
    start_ms: int
    end_ms: int
    layout: str
    facecam_roi: Rectangle | None = None
    gameplay_roi: Rectangle | None = None
    fit_facecam: bool = True
    preserve_gameplay: bool = True
    gameplay_path: tuple[tuple[int, int, int], ...] = ()
    camera_fraction: float = .35
    camera_slot: str = "top"


def crop_position(path: tuple[tuple[int, int, int], ...], time_ms: int) -> tuple[int, int]:
    """Evaluate source-clock crop keyframes, holding the first and last positions."""
    if time_ms <= path[0][0]:
        return tuple(path[0][1:])
    for (a, ax, ay), (b, bx, by) in zip(path, path[1:]):
        if time_ms <= b:
            f = (time_ms - a) / (b - a)
            return round(ax + (bx - ax) * f), round(ay + (by - ay) * f)
    return tuple(path[-1][1:])


def _crop_axis(path: tuple[tuple[int, int, int], ...], start_ms: int, axis: int) -> str:
    expr = str(path[-1][axis])
    for left, right in reversed(list(zip(path, path[1:]))):
        if left[axis] == right[axis]:
            value = str(left[axis])
        else:
            start = _seconds(left[0] - start_ms)
            length = _seconds(right[0] - left[0])
            value = f"{left[axis]}+({right[axis] - left[axis]})*clip((t-{start})/{length},0,1)"
        finish = _seconds(right[0] - start_ms)
        expr = f"if(lt(t,{finish}),{value},{expr})"
    return f"'{expr}'"


def exec_checked(args: list[str], *, timeout: float = 900, cwd: Path | None = None,
                 binary: bool = False) -> subprocess.CompletedProcess:
    try:
        kwargs = {} if binary else {"encoding": "utf-8", "errors": "replace"}
        return subprocess.run(args, capture_output=True, check=True, timeout=timeout, cwd=cwd, **kwargs)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing executable: {args[0]} (install FFmpeg and add it to PATH)") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{args[0]} timed out after {timeout:g} seconds") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr[-2000:]
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        raise RuntimeError(f"Command failed: {args[0]}: {stderr}") from exc


def probe(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    result = exec_checked(["ffprobe", "-v", "error", "-show_entries", "format=duration,start_time",
                           "-show_streams", "-of", "json", str(path)], timeout=60)
    info = json.loads(result.stdout)
    try:
        duration_ms = round(float(info["format"]["duration"]) * 1000)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Source has no known duration") from exc
    if duration_ms <= 0:
        raise ValueError("Source duration must be positive and finite")
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise ValueError("Source must contain an audio stream for the VOD baseline")
    return {"duration_ms": duration_ms, "streams": info["streams"]}


def check_audio_track(meta: dict, audio_stream: int) -> None:
    count = sum(s.get("codec_type") == "audio" for s in meta["streams"])
    if type(audio_stream) is not int or not 0 <= audio_stream < count:
        raise ValueError(f"Audio track {audio_stream} unavailable; source has {count} audio track(s)")


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


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.3f}"


def _audio_input(path: Path, audio_stream: int, start_ms: int = 0) -> list[str]:
    if type(audio_stream) is not int or audio_stream < 0:
        raise ValueError("audio_stream must be a nonnegative integer")
    return ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror",
            "-ss", _seconds(start_ms), "-i", str(path), "-map", f"0:a:{audio_stream}", "-vn"]


def decode_audio_window(path: Path, start_ms: int, end_ms: int, *, audio_stream: int = 0) -> bytes:
    """Decode a bounded window, padding delayed or exhausted tracks on its clock."""
    checked_span(start_ms, end_ms, end_ms)
    duration = end_ms - start_ms
    if duration > 10 * 60 * 1000:
        raise ValueError("Decode speech audio in windows of ten minutes or less")
    args = _audio_input(path, audio_stream, start_ms) + [
        "-af", SOURCE_CLOCK_AUDIO_FILTER + ",apad", "-t", _seconds(duration),
        "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1",
    ]
    result = exec_checked(args, timeout=max(60, duration / 1000), binary=True)
    bytes_expected = duration * 32
    return result.stdout[:bytes_expected].ljust(bytes_expected, b"\0")


def scan_audio(path: Path, *, audio_stream: int = 0, hop_ms: int = 1000,
               duration_ms: int | None = None) -> list[float]:
    """Stream source-clock audio levels without keeping the waveform in memory."""
    if type(hop_ms) is not int or hop_ms < 10:
        raise ValueError("Audio hop must be an integer of at least 10 ms")
    duration_ms = probe(path)["duration_ms"] if duration_ms is None else duration_ms
    cmd = _audio_input(path, audio_stream) + [
        "-af", SOURCE_CLOCK_AUDIO_FILTER + ",apad", "-t", _seconds(duration_ms),
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1",
    ]
    # A file-backed stderr buffer cannot fill a pipe while stdout is drained.
    with tempfile.TemporaryFile() as errors:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors)
        except FileNotFoundError as exc:
            raise RuntimeError("FFmpeg is required") from exc
        timed_out = threading.Event()

        def stop() -> None:
            timed_out.set()
            proc.kill()

        watchdog = threading.Timer(max(60, duration_ms / 1000), stop)
        watchdog.daemon = True
        watchdog.start()
        curve = []
        try:
            assert proc.stdout is not None
            with proc.stdout:
                while chunk := proc.stdout.read(SAMPLE_RATE * hop_ms // 1000 * 2):
                    curve.append(rms_dbfs(chunk))
            result = proc.wait()
        finally:
            watchdog.cancel()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if timed_out.is_set():
            raise RuntimeError("Audio scan timed out")
        if result:
            errors.seek(0, 2)
            errors.seek(max(0, errors.tell() - 2000))
            raise RuntimeError(f"Audio scan failed: {errors.read().decode('utf-8', 'replace')}")
    return curve


def exported_audio_level(path: Path) -> float:
    result = exec_checked(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path),
                           "-map", "0:a:0", "-vn", "-af", "volumedetect", "-f", "null", "-"])
    match = re.search(r"mean_volume:\s*(-?inf|-?\d+(?:\.\d+)?) dB", result.stderr)
    if match is None:
        raise RuntimeError("Unable to measure decoded export audio level")
    return float(match.group(1))


def exported_loudness(path: Path) -> dict:
    result = exec_checked(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path),
                           "-map", "0:a:0", "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    match = re.search(r'\{\s*"input_i".*?\}', result.stderr, flags=re.DOTALL)
    if not match:
        raise RuntimeError("Unable to measure decoded export loudness")
    result_data = json.loads(match.group())

    def finite(key: str) -> float | None:
        value = float(result_data[key])
        return value if math.isfinite(value) else None

    return {"integrated_lufs": finite("input_i"), "true_peak_dbtp": finite("input_tp")}


def resolve_layout(layout: str, facecam_roi: Rectangle | None) -> str:
    if layout not in LAYOUTS | {"auto"}:
        raise ValueError("Unknown layout")
    choice = ("facecam-gameplay" if facecam_roi else "gameplay-only") if layout == "auto" else layout
    if choice == "facecam-gameplay" and facecam_roi is None:
        raise ValueError("facecam-gameplay requires --facecam-roi x,y,w,h")
    return choice


def validate_roi(roi: Rectangle | None, vw: int, vh: int, name: str) -> Rectangle | None:
    if roi is None:
        return None
    if not isinstance(roi, (tuple, list)) or len(roi) != 4 or any(type(value) is not int for value in roi):
        raise ValueError(f"{name} must be a rectangle of four integers")
    x, y, w, h = roi
    if min(x, y) < 0 or min(w, h) <= 1 or x + w > vw or y + h > vh:
        raise ValueError(f"{name} must fit within the source video ({vw}x{vh})")
    return x, y, w, h


def _composite(inp: str, dst: str, seg: LayoutSegment, width: int, height: int, label: str,
               sar: Fraction) -> str:
    def crop(roi: Rectangle | None, path: tuple = ()) -> str:
        choice = ""
        if roi is not None:
            x, y, w, h = roi
            if path:
                x = _crop_axis(path, seg.start_ms, 1)
                y = _crop_axis(path, seg.start_ms, 2)
            choice = f"crop={w}:{h}:{x}:{y}:exact=1,"
        if sar != 1:
            choice += f"scale='max(2,trunc(iw*{sar}/2)*2)':ih,setsar=1,"
        return choice

    def scale(w: int, h: int, fill: bool) -> str:
        direction = "increase" if fill else "decrease"
        size = f"{w}:{h}"
        fit = f"scale={size}:force_original_aspect_ratio={direction}:force_divisible_by=2"
        edge = f"crop={size}" if fill else f"pad={size}:(ow-iw)/2:(oh-ih)/2:color=0x101419"
        return f"{fit},{edge},setsar=1"

    game_crop = crop(seg.gameplay_roi, seg.gameplay_path)
    if seg.layout == "facecam-gameplay":
        if seg.camera_slot in ("left", "right"):
            cam_w = round(width * .43 / 2) * 2
            cam_h = round(height * seg.camera_fraction * .75 / 2) * 2
            margin = round(width * .05)
            x = margin if seg.camera_slot == "left" else width - cam_w - margin
            y = round(height * .07)
            return (f"{inp}split=2[{label}cam][{label}game];"
                    f"[{label}cam]{crop(seg.facecam_roi)}{scale(cam_w, cam_h, not seg.fit_facecam)}[{label}inset];"
                    f"[{label}game]{game_crop}{scale(width, height, True)}[{label}main];"
                    f"[{label}main][{label}inset]overlay={x}:{y},format=yuv420p{dst}")
        camera_height = round(height * seg.camera_fraction / 2) * 2
        bottom = height - camera_height
        if seg.preserve_gameplay:
            low_w, low_h = max(2, width // 6 * 2), max(2, bottom // 6 * 2)
            game = (
                f"[{label}game]{game_crop}split=2[{label}sharp][{label}soft];"
                f"[{label}sharp]scale={width}:{bottom}:force_original_aspect_ratio=decrease:"
                f"force_divisible_by=2,setsar=1[{label}wide];"
                f"[{label}soft]{scale(low_w, low_h, True)},gblur=sigma=10,"
                f"eq=brightness=-0.30:saturation=0.5,scale={width}:{bottom},setsar=1[{label}matte];"
                f"[{label}matte][{label}wide]overlay=(W-w)/2:(H-h)/6[{label}bottom];"
            )
        else:
            game = f"[{label}game]{game_crop}{scale(width, bottom, True)}[{label}bottom];"
        order = (f"[{label}bottom][{label}top]" if seg.camera_slot == "bottom" else
                 f"[{label}top][{label}bottom]")
        return (f"{inp}split=2[{label}cam][{label}game];"
                f"[{label}cam]{crop(seg.facecam_roi)}{scale(width, camera_height, not seg.fit_facecam)}[{label}top];"
                f"{game}"
                f"{order}vstack=inputs=2,format=yuv420p{dst}")
    if seg.layout == "fit":
        blur_w, blur_h = max(2, width // 6 * 2), max(2, height // 6 * 2)
        return (f"{inp}{game_crop}split=2[{label}fg][{label}bg];"
                f"[{label}fg]scale={width}:{height}:force_original_aspect_ratio=decrease:"
                f"force_divisible_by=2,setsar=1[{label}fit];"
                f"[{label}bg]{scale(blur_w, blur_h, True)},gblur=sigma=10,"
                f"eq=brightness=-0.19:saturation=0.55,scale={width}:{height},setsar=1[{label}blur];"
                f"[{label}blur][{label}fit]overlay=(W-w)/2:(H-h)/2,format=yuv420p{dst}")
    return f"{inp}{game_crop}{scale(width, height, seg.layout == 'gameplay-only')},format=yuv420p{dst}"


def _hook_ass(title: str, width: int, height: int, y: int, length_ms: int) -> str:
    text = " ".join(title.split()).translate(str.maketrans({"{": "(", "}": ")", "\\": "∖"}))
    if len(text) > 72:
        raise ValueError("Shorten the hook title")
    words = text.split()
    if len(text) > 24 and len(words) > 1:
        options = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
        text = "\\N".join(min(options, key=lambda parts: max(map(len, parts))))
    end = min(175, length_ms // 10)
    if end <= 0:
        raise ValueError("The clip is too short for a hook")
    font = round(width * .064)
    return (
        f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Hook,Arial,{font},&H00EAFBFF,&H00EAFBFF,&H670B111A,&H670B111A,"
        f"-1,0,0,0,100,100,0,0,3,{max(3, width // 76)},0,5,0,0,0,1\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,0:00:{end // 100:02}.{end % 100:02},Hook,,0,0,0,,"
        f"{{\\pos({width // 2},{y})\\fad(80,120)}}{text}\n"
    )


def render_preview(
    source: Path, output: Path, start_ms: int, end_ms: int,
    width: int = 540, height: int = 960, *,
    captions_path: Path | None = None,
    facecam_roi: Rectangle | None = None,
    gameplay_roi: Rectangle | None = None,
    layout: str = "auto", audio_stream: int = 0, fit_facecam: bool = False,
    preserve_gameplay: bool = False, segments: tuple[LayoutSegment, ...] = (),
    caption_position: str = "lower", audio_profile: str = "original", shorts: bool = False,
    hook: str | None = None, sound_cues: tuple[SoundCue, ...] = (),
    shot_plan: dict | None = None, shot_evidence: dict | None = None,
) -> dict | None:
    """Render one continuous audio program and return technical evidence for ShotPlans."""
    def file_version(path: Path) -> tuple[int, ...]:
        info = path.stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    source_version = file_version(source) if shot_plan is not None else None
    meta = probe(source)
    checked_span(start_ms, end_ms, meta["duration_ms"])
    video = next((stream for stream in meta["streams"] if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("The source has no video stream to render")
    if any(type(size) is not int or size < 8 or size % 2 for size in (width, height)):
        raise ValueError("Output width/height must be positive even numbers")
    if audio_profile not in {"original", "balanced"} or caption_position not in {"upper", "lower", "middle"}:
        raise ValueError("Unknown audio profile or caption position")
    check_audio_track(meta, audio_stream)
    vw, vh = int(video["width"]), int(video["height"])
    rotation = next((int(entry["rotation"]) for entry in video.get("side_data_list", [])
                     if "rotation" in entry), 0)
    if rotation % 180 == 90:
        vw, vh = vh, vw
    try:
        sar = Fraction(video.get("sample_aspect_ratio", "1:1").replace(":", "/"))
        if sar <= 0:
            sar = Fraction(1)
    except (ValueError, ZeroDivisionError):
        sar = Fraction(1)
    if rotation % 180 == 90:
        sar = 1 / sar
    compiled_shots = None
    if shot_plan is not None:
        from .shotcomposer.compiler import compile_plan
        from .shotcomposer.workflow import verify_source

        if shot_evidence is None:
            raise ValueError("A Director shot plan requires its independent evidence ledger")
        if segments or facecam_roi or gameplay_roi or hook or sound_cues or audio_profile != "original":
            raise ValueError("ShotComposer owns visual composition and preserves original audio; remove conflicting overrides")
        if (shot_plan["start_ms"], shot_plan["end_ms"], shot_plan["canvas"]["width"], shot_plan["canvas"]["height"],
                shot_evidence["source"]["audio_stream"]) != (start_ms, end_ms, width, height, audio_stream):
            raise ValueError("Render arguments disagree with the Director shot plan")
        verify_source(source, shot_evidence, meta=meta)
        if file_version(source) != source_version:
            raise ValueError("Source changed while preparing the ShotPlan")
        compiled_shots = compile_plan(shot_plan, shot_evidence)
    elif shot_evidence is not None:
        raise ValueError("Shot evidence requires a shot plan")
    if not segments:
        segments = (LayoutSegment(start_ms, end_ms, resolve_layout(layout, facecam_roi), facecam_roi,
                                  gameplay_roi, fit_facecam, preserve_gameplay),)
    if len(segments) > 24:
        raise ValueError("At most 24 layout segments are supported per clip")
    cursor = start_ms
    for seg in segments:
        checked_span(seg.start_ms, seg.end_ms, meta["duration_ms"])
        if seg.start_ms != cursor or seg.end_ms > end_ms:
            raise ValueError("Layout segments must cover the clip without gaps or overlaps")
        if seg.layout not in LAYOUTS:
            raise ValueError("Layout segments must specify a concrete layout")
        resolve_layout(seg.layout, seg.facecam_roi)
        validate_roi(seg.facecam_roi, vw, vh, "Facecam ROI")
        game = validate_roi(seg.gameplay_roi, vw, vh, "Gameplay ROI")
        if (type(seg.camera_fraction) not in (float, int) or not .25 <= seg.camera_fraction <= .35
                or seg.camera_slot not in {"top", "bottom", "left", "right"}):
            raise ValueError("Camera slot and size are invalid")
        if seg.gameplay_path:
            if game is None or len(seg.gameplay_path) > 200:
                raise ValueError("Animated gameplay needs a crop and at most 200 keyframes per segment")
            last = seg.start_ms - 1
            for point in seg.gameplay_path:
                if not isinstance(point, (list, tuple)) or len(point) != 3 or any(type(v) is not int for v in point):
                    raise ValueError("Gameplay keyframes require integer time, x and y")
                time_ms, x, y = point
                if not last < time_ms <= seg.end_ms:
                    raise ValueError("Gameplay keyframes must be ordered inside their segment")
                validate_roi((x, y, game[2], game[3]), vw, vh, "Gameplay keyframe")
                last = time_ms
        cursor = seg.end_ms
    if cursor != end_ms:
        raise ValueError("Layout segments must end with the clip")

    duration = _seconds(end_ms - start_ms)
    source = source.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if source == output or (output.exists() and source.samefile(output)):
        raise ValueError("Output must be separate from the source")

    # A full-source Shorts fallback uses the same frame grid as temporal cuts.
    # A seek between source frames otherwise shifts its first frame and can drop the last.
    full_source_shorts = (compiled_shots is None and shorts and len(segments) == 1
                          and segments[0].layout == "letterbox" and segments[0].gameplay_roi is None)
    if compiled_shots is not None:
        graph = compiled_shots.graph
    elif len(segments) == 1 and not full_source_shorts:
        graph = _composite("[0:v:0]", "[stage]", segments[0], width, height, "a", sar)
    else:
        try:
            rate = Fraction(video["avg_frame_rate"])
            if not 1 <= rate <= 120:
                raise ValueError
        except (ValueError, ZeroDivisionError, KeyError):
            rate = Fraction(30)
        if any((seg.end_ms - seg.start_ms) * rate < 1000 for seg in segments):
            raise ValueError("Temporal layout segment is shorter than one source frame")
        clock = f"[0:v:0]fps={rate}:start_time=0"
        if full_source_shorts:
            graph = (f"{clock},trim=end={duration}[full_source];"
                     + _composite("[full_source]", "[stage]", segments[0], width, height, "a", sar))
        else:
            inputs = "".join(f"[input{i}]" for i in range(len(segments)))
            filters = [f"{clock},split={len(segments)}{inputs}"]
            for i, seg in enumerate(segments):
                begin = _seconds(seg.start_ms - start_ms)
                finish = _seconds(seg.end_ms - start_ms)
                # An end trim on each split branch lets concat finish the branch at the cut.
                filters.append(f"[input{i}]trim=start={begin}:end={finish},setpts=PTS-STARTPTS[part{i}]")
                filters.append(_composite(f"[part{i}]", f"[view{i}]", seg, width, height, f"x{i}", sar))
            views = "".join(f"[view{i}]" for i in range(len(segments)))
            filters.append(f"{views}concat=n={len(segments)}:v=1:a=0[stage]")
            graph = ";".join(filters)

    with tempfile.TemporaryDirectory(prefix=".render-", dir=output.parent) as scratch, staged_file(output) as staged:
        scratch = Path(scratch)
        stage = "[stage]"
        if hook:
            opening = segments[0]
            seam = (opening.camera_fraction if opening.layout == "facecam-gameplay"
                    and opening.camera_slot == "top" else .0)
            y = round(height * (seam + .105 if seam else .13))
            (scratch / "hook.ass").write_text(_hook_ass(hook, width, height, y, end_ms - start_ms), encoding="utf-8")
            graph += f";{stage}ass=filename=hook.ass[hook]"
            stage = "[hook]"
        if captions_path is not None:
            if not captions_path.is_file():
                raise ValueError(f"Caption file does not exist: {captions_path}")
            if captions_path.stat().st_size:
                shutil.copyfile(captions_path, scratch / "captions.srt")
                alignment = {"lower": 2, "middle": 5, "upper": 8}[caption_position]
                if shorts:
                    if caption_position == "lower" and any(s.camera_slot == "bottom" for s in segments):
                        alignment = 5
                    style = (f"FontName=Arial,Bold=-1,FontSize=11,Alignment={alignment},"
                             "MarginV=58,MarginL=23,MarginR=29,Outline=1.6,Shadow=.5")
                else:
                    style = (f"FontName=Arial,FontSize=11,Alignment={alignment},"
                             "MarginV=30,MarginL=22,MarginR=24,Outline=1.4,Shadow=0")
                graph += f";{stage}subtitles=filename=captions.srt:force_style='{style}'[out]"
            else:
                graph += f";{stage}null[out]"
        else:
            graph += f";{stage}null[out]"

        audio_filter = SOURCE_CLOCK_AUDIO_FILTER + f",apad,atrim=duration={duration}"
        loudness = ",loudnorm=I=-16:TP=-1.5:LRA=11" if audio_profile == "balanced" else ""
        audio_inputs, audio_map = [], ["-map", f"0:a:{audio_stream}", "-af", audio_filter + loudness]
        active_cues = cues_in_range(sound_cues, start_ms, end_ms)
        if active_cues:
            stereo = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
            graph += f";[0:a:{audio_stream}]{audio_filter},{stereo}[sourceaudio]"
            labels = ["[sourceaudio]"]
            for i, cue in enumerate(active_cues, 1):
                audio_inputs.extend(("-protocol_whitelist", "file,pipe", "-i", cue.path))
                label = f"[sound{i}]"
                labels.append(label)
                length = cue.duration_ms / 1000
                fade = min(.012, length / 4)
                graph += (f";[{i}:a:0]atrim=start={_seconds(cue.offset_ms)}:duration={length:.3f},"
                          f"asetpts=PTS-STARTPTS,{stereo},afade=t=in:d={fade:.6f},"
                          f"afade=t=out:st={length - fade:.6f}:d={fade:.6f},"
                          f"volume={float(cue.gain_db):.3f}dB,adelay={cue.at_ms - start_ms}:all=1{label}")
            graph += (";" + "".join(labels) + f"amix=inputs={len(labels)}:duration=first:"
                      f"dropout_transition=0:normalize=0,alimiter=limit=.84:level=false:latency=true{loudness}[mix]")
            audio_map = ["-map", "[mix]"]
        elif len(segments) > 1 or full_source_shorts:
            # FFmpeg 8.0.1 can stall on split/concat video with a separate
            # audio filter graph when the selected audio stream starts late.
            graph += f";[0:a:{audio_stream}]{audio_filter}{loudness}[selected_audio]"
            audio_map = ["-map", "[selected_audio]"]
        graph_args = ["-filter_complex", graph]
        if len(graph) > 9000:
            (scratch / "video-filters.txt").write_text(graph, encoding="ascii")
            graph_args = ["-filter_complex_script", "video-filters.txt"]
        encoding = (["-b:v", "12M", "-maxrate", "18M", "-bufsize", "24M"]
                    if shorts and width >= 1080 else ["-crf", "20" if width >= 1080 else "23"])
        # These graphs bound both media streams. Muxer -t rounding can discard
        # the last legal video frame.
        time_limit = ["-t", duration] if compiled_shots is None and not full_source_shorts else []
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error", "-xerror",
                "-ss", _seconds(start_ms), "-i", str(source), *audio_inputs, *time_limit,
                "-filter_complex_threads", "1", *graph_args,
                "-map", "[out]", *audio_map,
                "-c:v", "libx264", "-preset", "veryfast", *encoding,
                "-pix_fmt", "yuv420p", "-fps_mode", "vfr", "-c:a", "aac", "-b:a", "160k",
                "-ar", "48000", "-ac", "2", "-disposition:a:0", "default",
                "-movflags", "+faststart", str(staged)]
        timeout = max(60, (end_ms - start_ms) / 1000 * 12)
        exec_checked(args, cwd=scratch, timeout=timeout)
        rendered = probe(staged)
        outvideo = next((s for s in rendered["streams"] if s.get("codec_type") == "video"), {})
        if (outvideo.get("codec_name"), outvideo.get("width"), outvideo.get("height")) != ("h264", width, height):
            raise RuntimeError("Rendered video did not pass H.264 portrait verification")
        if not any(s.get("codec_type") == "audio" and s.get("codec_name") == "aac" for s in rendered["streams"]):
            raise RuntimeError("Rendered clip is missing AAC audio")
        expected = end_ms - start_ms
        duration_tolerance = 150
        shot_report = None
        if compiled_shots is not None:
            from .shotcomposer.contract import fraction

            rate = fraction(shot_plan["canvas"]["fps"])
            duration_tolerance = max(25, math.ceil(1000 / rate))
            expected_frames = compiled_shots.report["requested_vs_realized_timing"][-1]["end_frame_exclusive"]
            if outvideo.get("nb_frames") not in (None, "N/A", str(expected_frames), expected_frames):
                raise RuntimeError(f"Rendered {outvideo['nb_frames']} frames; ShotPlan needs {expected_frames}")
            if outvideo.get("pix_fmt") != "yuv420p" or outvideo.get("sample_aspect_ratio") != "1:1":
                raise RuntimeError("Rendered video did not preserve the ShotPlan pixel format and aspect ratio")
            shot_report = {**compiled_shots.report, "output_media": {
                "duration_ms": rendered["duration_ms"], "target_frame_count": expected_frames,
                "video": {k: outvideo.get(k) for k in ("codec_name", "width", "height", "pix_fmt",
                                                        "sample_aspect_ratio", "avg_frame_rate",
                                                        "time_base", "nb_frames", "duration")},
                "audio": [{k: s.get(k) for k in ("codec_name", "sample_rate", "channels", "start_time", "duration")}
                          for s in rendered["streams"] if s.get("codec_type") == "audio"],
            }}
        if abs(rendered["duration_ms"] - expected) > duration_tolerance:
            raise RuntimeError("Rendered clip has unexpected duration; verify source timestamps")
        if outvideo.get("duration") not in (None, "N/A"):
            if abs(float(outvideo["duration"]) * 1000 - expected) > duration_tolerance:
                raise RuntimeError("Rendered video ended early or ran past the clip")
        exec_checked(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror",
                      "-i", str(staged), "-f", "null", "-"], timeout=timeout)
        if any(digest_file(Path(path)) != digest for path, digest in {(cue.path, cue.sha256) for cue in active_cues}):
            raise ValueError("A sound cue asset changed during rendering")
        if compiled_shots is not None and file_version(source) != source_version:
            raise ValueError("Source changed during ShotPlan rendering")
        staged.replace(output)
    return shot_report
