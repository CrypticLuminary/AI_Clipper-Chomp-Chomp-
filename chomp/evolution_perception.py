"""Bounded local acoustic and visual observations on the source clock."""
from __future__ import annotations

from hashlib import sha256
from importlib.metadata import version
from math import ceil
from pathlib import Path
from zipfile import ZipFile

from .artifacts import artifact_key, artifact_lock, digest_file, staged_file, write_json
from .director.contract import read_json
from .director.evidence import extract_frame
from .media import decode_audio_window, exec_checked
from .research_interfaces import evidence_item, validate_evidence

YAMNET_REVISION = "fcd63e19bd0a7a0d3d8c9c070af1898f7dd29675"
YAMNET_SHA256 = "4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf"


def cached_observations(cache: Path, fingerprint: dict, build) -> tuple[list[dict], bool]:
    key = artifact_key(fingerprint)
    path = cache / "evolution-observations" / f"{key}.json"
    with artifact_lock(cache / "locks" / f"{key}.lock", timeout=600):
        if path.is_file():
            try:
                data = read_json(path)
                if data["fingerprint"] == fingerprint:
                    rows = [validate_evidence(r, fingerprint["source_id"], fingerprint["duration_ms"])
                            for r in data["rows"]]
                    return rows, True
            except (OSError, ValueError, TypeError, KeyError):
                pass
        rows = build()
        write_json(path, {"fingerprint": fingerprint, "rows": rows})
        return rows, False


def yamnet(source: Path, model: Path, cache: Path, identity: dict, audio_stream: int,
           max_windows: int) -> tuple[list[dict], dict]:
    import numpy as np
    from ai_edge_litert.interpreter import Interpreter

    if digest_file(model) != YAMNET_SHA256:
        raise ValueError("YAMNet weights differ from the supported checkpoint")
    duration = identity["duration_ms"]
    window, hop = 975, 480
    total = max(1, ceil(max(0, duration - window) / hop) + 1)
    # Increase the stride across the whole recording when the budget is smaller than the source.
    stride = ceil(total / max_windows)
    fingerprint = {**identity, "producer": "yamnet-tflite/1", "model_revision": YAMNET_REVISION,
                   "runtime": version("ai-edge-litert"), "window_ms": window, "hop_ms": hop * stride,
                   "audio_stream": audio_stream, "sample_rate": 16000}

    def analyze():
        with ZipFile(model) as asset:
            labels = asset.read("yamnet_label_list.txt").decode().splitlines()
        engine = Interpreter(model_path=str(model), num_threads=2)
        engine.allocate_tensors()
        inp = engine.get_input_details()[0]["index"]
        out = engine.get_output_details()[0]["index"]
        merged = []
        starts = range(0, total * hop, hop * stride)
        batch_size = min(64, 30_000 // (hop * stride) + 1)
        for first in range(0, len(starts), batch_size):
            batch = starts[first:first + batch_size]
            stop = min(duration, batch[-1] + window)
            raw = decode_audio_window(source, batch[0], stop, audio_stream=audio_stream)
            pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
            for start in batch:
                end = min(duration, start + window)
                offset = (start - batch[0]) * 16
                samples = pcm[offset:offset + window * 16]
                if samples.size < window * 16:
                    samples = np.pad(samples, (0, window * 16 - samples.size))
                if np.max(np.abs(samples)) < .0002:
                    continue
                engine.set_tensor(inp, samples)
                engine.invoke()
                scores = engine.get_tensor(out)[0]
                observations = {labels[i]: round(float(scores[i]), 5)
                                for i in np.argsort(-scores)[:5] if scores[i] >= .15}
                if not observations:
                    continue
                lead = next(iter(observations))
                if merged and merged[-1][2] == lead and merged[-1][1] >= start and end - merged[-1][0] <= 8_000:
                    prior = merged[-1][3]
                    merged[-1] = (merged[-1][0], end, lead,
                                  {label: max(prior.get(label, 0), observations.get(label, 0))
                                   for label in dict.fromkeys([*prior, *observations])})
                else:
                    merged.append((start, end, lead, observations))
        return [evidence_item(**identity, start_ms=start, end_ms=end, modality="acoustic_event",
                              model_fingerprint=fingerprint, provenance={"original": "selected_source_pcm"},
                              audio_stream=audio_stream, labels=[{"label": label, "confidence": score}
                                  for label, score in scores.items()], confidence=max(scores.values()))
                for start, end, _, scores in merged]

    rows, hit = cached_observations(cache, fingerprint, analyze)
    count = len(range(0, total, stride))
    covered = (min(duration, (count - 1) * hop * stride + window)
               if hop * stride <= window else (count - 1) * window + min(window, duration - (count - 1) * hop * stride))
    return rows, {"state": "available", "cache_hit": hit, "coverage_ms": covered,
                  "samples": count, "fingerprint": fingerprint}


def visual_motion(source: Path, cache: Path, identity: dict, step_ms: int,
                  max_frames: int) -> tuple[list[dict], dict]:
    duration = identity["duration_ms"]
    interval = max(step_ms, ceil(duration / max_frames))
    w, h = 64, 36
    decoder = exec_checked(["ffmpeg", "-version"], timeout=10).stdout.splitlines()[0]
    fingerprint = {**identity, "producer": "uniform-luma-difference/1", "decoder": decoder,
                   "interval_ms": interval, "dimensions": [w, h], "max_frames": max_frames}

    def analyze():
        out = exec_checked(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(source), "-map", "0:v:0",
                            "-an", "-filter_threads", "1", "-vf",
                            f"fps=1000/{interval}:start_time=0:round=up,scale={w}:{h}:flags=area,format=gray",
                            "-frames:v", str(max_frames), "-pix_fmt", "gray", "-threads:v", "1",
                            "-f", "rawvideo", "pipe:1"], binary=True, timeout=max(60, duration / 1000))
        raw = out.stdout
        size = w * h
        if not raw or len(raw) % size:
            raise ValueError("Visual sampling produced no complete frames")
        rows = []
        prior = raw[:size]
        for sample in range(0, len(raw) // size):
            start, end = sample * interval, min(duration, (sample + 1) * interval)
            if end <= start:
                break
            frame = raw[sample * size:(sample + 1) * size]
            change = sum(abs(a - b) for a, b in zip(prior, frame)) / (size * 255)
            rows.append(evidence_item(**identity, start_ms=start, end_ms=end,
                modality="scene_transition" if change >= .23 else "motion",
                model_fingerprint=fingerprint, provenance={"original": "video:0", "clock": "sampling_bin"},
                strength=round(change, 5), sample_index=sample))
            prior = frame
        return rows

    rows, hit = cached_observations(cache, fingerprint, analyze)
    return rows, {"state": "available", "cache_hit": hit, "sample_count": len(rows), "sample_interval_ms": interval,
                  "coverage": "sparse_uniform", "frame_content_between_samples": "unobserved"}


def visual_frames(source: Path, cache: Path, identity: dict, timestamps: list[int], end: int):
    decoder = exec_checked(["ffmpeg", "-version"], timeout=10).stdout.splitlines()[0]
    model = {"producer": "source-frame/1", "decoder": decoder}
    results = []
    for requested in sorted(set(timestamps)):
        key = artifact_key([identity, model, requested])
        path = cache / "evolution-frames" / f"{key}.png"
        meta = path.with_suffix(".json")
        with artifact_lock(cache / "locks" / f"{key}.lock"):
            if meta.is_file() and path.is_file():
                at, sha = read_json(meta)
                if digest_file(path) != sha:
                    raise ValueError("Visual evidence cache integrity check failed")
                pixels = path.read_bytes()
            else:
                pixels, at, _, _ = extract_frame(source, requested)
                with staged_file(path) as pending:
                    pending.write_bytes(pixels)
                    pending.replace(path)
                sha = sha256(pixels).hexdigest()
                write_json(meta, [at, sha])
            if not requested <= at < end:
                continue
        results.append((evidence_item(**identity, start_ms=at, end_ms=at + 1, modality="visual_frame",
                         model_fingerprint=model, provenance={"original": "video:0", "frame_sha256": sha,
                                                               "cache_file": path.name}), pixels))
    return results
