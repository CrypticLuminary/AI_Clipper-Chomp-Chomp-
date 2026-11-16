"""Freeze and verify a small set of actual source observations."""
from __future__ import annotations

from dataclasses import asdict
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from ..artifacts import artifact_lock, source_identity, write_json
from ..media import check_audio_track, decode_audio_window, exec_checked, probe, rms_dbfs
from ..models import checked_span
from ..signals import audio_observations, chat_observations, read_chat, read_transcript
from .contract import bundle_digest, read_json, validate_bundle_shape

_FRAME_TIME_BASE = re.compile(r"config in time_base:\s*(\d+/\d+)")
_FRAME_INFO = re.compile(r"\bn:\s*0\s+pts:\s*(-?\d+).*?\bs:(\d+)x(\d+)")
_HOP_MS = 250
_PCM_BYTES_PER_HOP = 8000  # 16 kHz mono s16le from the shared selected-track decoder.


def extract_frame(source: Path, requested_ms: int) -> tuple[bytes, int, int, int]:
    """Copy the first decoded frame on or after a source-clock seek."""
    result = exec_checked([
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info", "-xerror",
        "-copyts", "-start_at_zero", "-ss", f"{requested_ms / 1000:.3f}", "-i", str(source),
        "-map", "0:v:0", "-an", "-sn", "-dn", "-frames:v", "1", "-filter_threads", "1",
        "-vf", "scale=w='min(960,iw)':h=-2:flags=area,showinfo",
        "-fps_mode", "passthrough", "-c:v", "png", "-threads:v", "1", "-f", "image2pipe", "pipe:1",
    ], timeout=25, binary=True)
    log = result.stderr.decode("utf-8", "replace")
    time_base = _FRAME_TIME_BASE.search(log)
    frame = _FRAME_INFO.search(log)
    if time_base is None or frame is None or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"Source has no verifiable image at {requested_ms} ms")
    pts, width, height = map(int, frame.groups())
    return result.stdout, round(1000 * pts * Fraction(time_base[1])), width, height


def _source_metadata(source: Path, audio_stream: int) -> dict:
    if not source.is_file():
        raise ValueError("Supply a local regular VOD file")
    meta = probe(source)
    check_audio_track(meta, audio_stream)
    video = next((row for row in meta["streams"] if row["codec_type"] == "video"), None)
    if video is None:
        raise ValueError("The source must have a video stream")
    result = {"sha256": source_identity(source).removeprefix("sha256:"), "duration_ms": meta["duration_ms"],
              "width": int(video["width"]), "height": int(video["height"]), "audio_stream": audio_stream}
    rotation = next((int(row["rotation"]) for row in video.get("side_data_list", []) if "rotation" in row), 0)
    if rotation % 180 == 90:
        result["width"], result["height"] = result["height"], result["width"]
    return result


def _observation_rows(observations, prefix, start, end, offset=0) -> list[dict]:
    usable = [row for row in observations if start <= row.start_ms + offset < row.end_ms + offset <= end]
    strongest = sorted(usable, key=lambda row: (-row.strength, row.start_ms))[:80]
    return [
        {"id": f"{prefix}{i}", "start_ms": row.start_ms + offset, "end_ms": row.end_ms + offset,
         "kind": row.kind, "strength": row.strength, "measurement": row.evidence}
        for i, row in enumerate(sorted(strongest, key=lambda row: row.start_ms), 1)
    ]


def _utterance_rows(transcript, start, end) -> list[dict]:
    rows = []
    for i, utterance in enumerate(transcript, 1):
        if not (start <= utterance.start_ms < utterance.end_ms <= end and utterance.text.strip()):
            continue
        uid = f"u{i}"
        rows.append({
            "id": uid, "start_ms": utterance.start_ms, "end_ms": utterance.end_ms,
            "text": utterance.text, "speaker_id": utterance.speaker_id,
            "words": [{**asdict(word), "id": f"{uid}-w{j}"} for j, word in enumerate(utterance.words, 1)],
        })
    if len(rows) > 80:
        raise ValueError("Use a smaller evidence window to keep all included utterances")
    return rows


def _local_stat(source: Path) -> tuple[int, int]:
    stat = source.stat()
    return stat.st_size, stat.st_mtime_ns


def build_bundle(
    source: Path, destination: Path, *, case_id: str, split: str, rights_ref: str, candidate_id: str,
    start_ms: int, end_ms: int, anchor_ms: int, context_before_ms: int = 10_000, context_after_ms: int = 10_000,
    audio_stream: int = 0, transcript: Path | None = None, chat: Path | None = None,
    chat_offset_ms: int = 0, frame_times: tuple[int, ...] = (), regions: Path | None = None,
) -> Path:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not rights_ref.strip():
        raise ValueError("Supply the authorization reference for private evidence analysis")
    before = _local_stat(source)
    meta = _source_metadata(source, audio_stream)
    duration = meta["duration_ms"]
    checked_span(start_ms, end_ms, duration)
    if not start_ms <= anchor_ms < end_ms:
        raise ValueError("Candidate anchor must be inside the proposed interval")
    if min(context_before_ms, context_after_ms) < 0:
        raise ValueError("Evidence context padding cannot be negative")
    start = max(0, start_ms - context_before_ms)
    end = min(duration, end_ms + context_after_ms)
    if end - start > 120_000:
        raise ValueError("Evidence context exceeds 120 seconds")
    times = sorted(frame_times)
    if len(times) > 8 or len(times) != len(set(times)) or any(not start <= t < end for t in times):
        raise ValueError("Supply up to eight distinct frame times inside the evidence context")
    annotations = read_json(regions) if regions else {}
    if not isinstance(annotations, dict) or annotations.keys() - {str(t) for t in times}:
        raise ValueError("Region annotations must be keyed by requested frame time in milliseconds")
    if any(not isinstance(items, list) or any(not isinstance(item, dict) or "id" in item for item in items)
           for items in annotations.values()):
        raise ValueError("Supply region annotation arrays without region IDs")

    speech = _utterance_rows(read_transcript(transcript, duration), start, end)
    log = read_chat(chat, duration, chat_offset_ms)
    raw = decode_audio_window(source, start, end, audio_stream=audio_stream)
    levels = [max(-120., rms_dbfs(raw[i:i + _PCM_BYTES_PER_HOP])) for i in range(0, len(raw), _PCM_BYTES_PER_HOP)]
    audio = _observation_rows(audio_observations(levels, end - start, hop_ms=_HOP_MS), "a", start, end, start)
    chat_rows = _observation_rows(chat_observations(log, duration), "c", start, end)
    ffmpeg_version = exec_checked(["ffmpeg", "-version"], timeout=10).stdout.splitlines()[0]

    destination.parent.mkdir(parents=True, exist_ok=True)
    with artifact_lock(destination.parent / f".{destination.name}.lock", timeout=5):
        if destination.exists():
            raise FileExistsError(f"Choose a new location for immutable evidence: {destination}")
        with TemporaryDirectory(prefix=".evidence-", dir=destination.parent) as temp:
            folder = Path(temp)
            (folder / "frames").mkdir()
            frames = []
            for i, requested in enumerate(times, 1):
                pixels, at, width, height = extract_frame(source, requested)
                fid = f"f{i}"
                filename = f"frames/{fid}.png"
                (folder / filename).write_bytes(pixels)
                scene = {"id": f"{fid}-scene", "subject_id": "scene", "kind": "scene",
                         "box": [0, 0, width, height], "confidence": 1, "origin": "source_bounds", "verified": True}
                marked = [{"id": f"{fid}-r{j}", **region}
                          for j, region in enumerate(annotations.get(str(requested), []), 1)]
                frames.append({
                    "id": fid, "requested_ms": requested, "at_ms": at, "path": filename,
                    "sha256": sha256(pixels).hexdigest(), "width": width, "height": height,
                    "scene_id": None, "regions": [scene, *marked],
                })
            if _local_stat(source) != before:
                raise ValueError("Source changed while the evidence was collected")
            bundle = {
                "schema_version": "chomp-evidence/1",
                "source": {**meta, "case_id": case_id, "split": split, "rights_ref": rights_ref,
                           "ffmpeg_version": ffmpeg_version},
                "candidate": {"id": candidate_id, "start_ms": start_ms, "end_ms": end_ms, "anchor_ms": anchor_ms},
                "context": {"start_ms": start, "end_ms": end},
                "modality_flags": {"transcript": transcript is not None, "audio": bool(raw),
                                   "chat": chat is not None, "visual": bool(frames)},
                "utterances": speech, "audio_events": audio, "chat_events": chat_rows, "visual_samples": frames,
            }
            bundle["evidence_hash"] = bundle_digest(bundle)
            validate_bundle_shape(bundle)
            write_json(folder / "evidence.json", bundle)
            folder.rename(destination)
    return destination / "evidence.json"


def read_verified_bundle(path: Path, source: Path) -> tuple[dict, list[bytes]]:
    """Prove the frozen image bytes came from the identified source at the stated times."""
    bundle = read_json(path)
    validate_bundle_shape(bundle)
    source = source.expanduser().resolve()
    before = _local_stat(source)
    expected = bundle["source"]
    actual = _source_metadata(source, expected["audio_stream"])
    if any(expected[key] != value for key, value in actual.items()):
        raise ValueError("Evidence source hash or probed media properties do not match")
    root = path.resolve().parent
    folder = root / "frames"
    if folder.resolve() != folder:
        raise ValueError("Evidence images must reside in the bundle's local frames folder")
    images = []
    for frame in bundle["visual_samples"]:
        asset = root / frame["path"]
        if asset.resolve() != asset:
            raise ValueError("Evidence image cannot link outside its recorded path")
        pixels = asset.read_bytes()
        if sha256(pixels).hexdigest() != frame["sha256"]:
            raise ValueError(f"Image hash does not match for {frame['id']}")
        decoded = extract_frame(source, frame["requested_ms"])
        if decoded != (pixels, frame["at_ms"], frame["width"], frame["height"]):
            raise ValueError(f"{frame['id']} does not match a decoded source image")
        images.append(pixels)
    if _local_stat(source) != before:
        raise ValueError("Source changed during evidence verification")
    return bundle, images
