"""Speaker diarization subsystem — optional heavyweight dependency.

This package provides anonymous speaker segmentation for gaming and livestream
recordings.  It outputs timestamped speaker turns using the existing
``SpeakerTurn`` contract, which the pipeline can consume via ``--speaker-turns``.

The diarization backend is **optional**: the rest of Chomp-Chomp works without
it.  Install the ``[diarize]`` extra to enable a backend.

Two backends are supported:

- **sherpa-onnx** (preferred): Apache 2.0, ONNX Runtime, no PyTorch, ~34 MB
  models, native Windows wheels, complete pipeline.  Install: ``pip install
  sherpa-onnx``
- **resemblyzer** (fallback): Apache 2.0, requires PyTorch, ~16 MB model,
  requires scikit-learn for clustering.  Install: ``pip install
  'chomp-chomp[diarize]'``

Speaker labels are anonymous (``SPEAKER_00``, ``SPEAKER_01``, …).
Identifying the main streamer requires explicit confirmation via
``--main-speaker-id`` after reviewing the output; automatic identification
is never assumed.
"""
from __future__ import annotations

from .core import (
    DiarizationConfig,
    DiarizationResult,
    diarize_audio,
    check_backend_available,
    BACKEND_UNAVAILABLE_REASON,
)
from .identity import SpeakerIdentityMap, build_identity_map
from .integration import (
    diarize_and_export,
    merge_diarization_with_transcript,
)

__all__ = [
    "DiarizationConfig",
    "DiarizationResult",
    "diarize_audio",
    "check_backend_available",
    "BACKEND_UNAVAILABLE_REASON",
    "SpeakerIdentityMap",
    "build_identity_map",
    "diarize_and_export",
    "merge_diarization_with_transcript",
]
