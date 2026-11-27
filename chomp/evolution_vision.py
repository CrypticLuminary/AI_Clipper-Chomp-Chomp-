"""Sparse CLIP vision embeddings for independent, budgeted scene-change proposals."""
from __future__ import annotations

from importlib.metadata import version
from math import ceil
from pathlib import Path

from .artifacts import digest_file
from .evolution_perception import cached_observations, visual_frames
from .research_interfaces import evidence_item

CLIP_REVISION = "6b7c523703ae26e39be4b03774966f8571c7f523"
CLIP_SHA256 = "b95448754a6ae56964ec80c570c80bb9863787ee85f51b664abed8c0e5f22a7a"


def clip_vision(source: Path, cache: Path, identity: dict, model: Path,
                motion: list[dict], cap: int, use_cuda: bool) -> tuple[list[dict], dict]:
    import cv2
    import numpy as np
    import onnxruntime as ort

    if digest_file(model) != CLIP_SHA256:
        raise ValueError("CLIP weights differ from the supported checkpoint")
    duration = identity["duration_ms"]
    uniform = max(1, ceil(cap * .75))
    times = set(round((duration - 1) * i / uniform) for i in range(uniform))
    separation = max(1, duration // max(1, cap * 3))
    for row in sorted(motion, key=lambda r: -r["strength"]):
        if len(times) >= cap or row["strength"] < .035:
            break
        for t in (row["start_ms"], min(row["end_ms"] - 1, duration - 1)):
            if len(times) < cap and not any(abs(t - chosen) < separation for chosen in times):
                times.add(t)
    timestamps = sorted(times)
    providers = ["CPUExecutionProvider"]
    if use_cuda and "CUDAExecutionProvider" in ort.get_available_providers():
        providers.insert(0, "CUDAExecutionProvider")
    fingerprint = {**identity, "producer": "clip-vision-uint8/1", "model_revision": CLIP_REVISION,
                   "runtime": version("onnxruntime"), "opencv": cv2.__version__,
                   "requested_providers": providers, "timestamps_ms": timestamps}

    def build():
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        try:
            engine = ort.InferenceSession(str(model), sess_options=options, providers=providers)
        except Exception as first:
            if providers == ["CPUExecutionProvider"]:
                raise RuntimeError(f"CLIP ONNX runtime failed to load: {first}") from first
            try:
                engine = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
            except Exception as second:
                raise RuntimeError(f"CLIP ONNX CPU fallback failed to load: {second}") from second
        actual_provider = engine.get_providers()[0]
        mean = np.asarray([.48145466, .4578275, .40821073], dtype=np.float32)
        spread = np.asarray([.26862954, .26130258, .27577711], dtype=np.float32)
        prior = None
        observations = []
        for i in range(0, len(timestamps), 4):
            sampled = visual_frames(source, cache, identity, timestamps[i:i + 4], duration)
            if not sampled:
                continue
            inputs = []
            for _, pixels in sampled:
                image = cv2.imdecode(np.frombuffer(pixels, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError("Could not decode a sampled CLIP frame")
                height, width = image.shape[:2]
                scale = 224 / min(height, width)
                resized = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_CUBIC)
                y, x = [(size - 224) // 2 for size in resized.shape[:2]]
                rgb = resized[y:y + 224, x:x + 224, ::-1].astype(np.float32) / 255
                inputs.append(((rgb - mean) / spread).transpose(2, 0, 1))
            batch = np.stack(inputs)
            try:
                vectors = engine.run(["image_embeds"], {"pixel_values": batch})[0]
            except Exception as exc:
                raise RuntimeError(f"CLIP ONNX inference failed: {exc}") from exc
            vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
            for (row, _), vector in zip(sampled, vectors):
                if observations and row["start_ms"] <= observations[-1]["start_ms"]:
                    continue
                change = 0 if prior is None else float(np.clip(1 - vector @ prior, 0., 1.))
                observations.append(evidence_item(**identity, start_ms=row["start_ms"], end_ms=row["end_ms"],
                    modality="visual_state", model_fingerprint={"producer": fingerprint["producer"],
                        "model_revision": CLIP_REVISION, "provider": actual_provider},
                    provenance={"original": "video:0", "frame_sha256": row["provenance"]["frame_sha256"],
                                "cache_file": row["provenance"]["cache_file"]},
                    strength=round(change, 5), embedding=vector.round(5).tolist(),
                    previous_observation_id=observations[-1]["id"] if observations else None))
                prior = vector
        return observations

    rows, hit = cached_observations(cache, fingerprint, build)
    return rows, {"state": "available" if rows else "unavailable", "sample_count": len(rows), "cache_hit": hit,
                  "coverage": "sparse_uniform_and_motion", "fingerprint": fingerprint,
                  "provider": rows[0]["model_fingerprint"]["provider"] if rows else None}