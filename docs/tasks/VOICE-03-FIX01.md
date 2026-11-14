# Task Report: VOICE-03-FIX01 — Fix sherpa-ONNX Diarization Model Loading

**Agent code:** GFLASH38-AG  
**Model:** Gemini 3.8 Flash  
**Environment:** Antigravity (Windows x64)  
**Parent task:** VOICE-03 / EchoMap  
**Task:** Fix sherpa-ONNX diarization model loading  
**Date:** 2026-11-14  

---

## 1. Problem Statement

In the initial implementation of Section 3 speaker diarization (`chomp/diarization/core.py`), the `sherpa-onnx` backend configuration passed empty strings for both the segmentation and speaker-embedding models (`model=""`):

```python
# Before fix:
sd_config = sherpa.OfflineSpeakerDiarizationConfig(
    segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
        pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
            model="",
        ),
        num_threads=2,
    ),
    embedding=sherpa.SpeakerEmbeddingExtractorConfig(
        model="",
        num_threads=2,
    ),
    ...
)
```

This caused two critical issues:
1. When attempting to instantiate `sherpa_onnx.OfflineSpeakerDiarization`, the runtime failed because valid ONNX model file paths were not supplied.
2. In automatic backend selection (`backend="auto"`), detecting `sherpa-onnx` as installed treated it as a fully usable model even when no model files were provided, blocking the resemblyzer fallback.

---

## 2. Sherpa-ONNX Python API Verification

The `sherpa-onnx` Python offline speaker diarization API requires:
1. **Segmentation Model:** An ONNX model (typically Pyannote segmentation v3.0, e.g. `sherpa-onnx-pyannote-segmentation-3-0/model.onnx`) configured via:
   ```python
   sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation_model_path))
   ```
2. **Embedding Model:** A speaker recognition ONNX model (e.g. 3D-Speaker or CAM++, `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx`) configured via:
   ```python
   sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding_model_path), num_threads=2)
   ```
3. **Clustering:** Configured via `sherpa_onnx.FastClusteringConfig(num_clusters=..., threshold=...)`.

Empty paths or non-existent files are rejected by the underlying ONNX Runtime / sherpa engine.

---

## 3. Changes Implemented

### 3.1 `chomp/diarization/core.py`
- **Configuration Fields:** Added `segmentation_model: Path | str | None = None` and `embedding_model: Path | str | None = None` to `DiarizationConfig`.
- **Validation:** Added `_validate_sherpa_models(config)`:
  - Validates that both `segmentation_model` and `embedding_model` are configured. Raises `ValueError` with clear guidance if either or both are missing.
  - Validates that the configured model paths actually exist on disk and are not directories. Raises `FileNotFoundError` with clear details if files are missing.
- **Model Path Propagation:** Updated `_diarize_sherpa()` to pass `str(seg_path)` and `str(emb_path)` to `OfflineSpeakerSegmentationPyannoteModelConfig` and `SpeakerEmbeddingExtractorConfig`. Recorded model paths in `result.metadata`.
- **Automatic Backend Selection Guard:** Updated `diarize_audio()` when `backend == "auto"`:
  - Verifies `_has_valid_sherpa_models(config)` before selecting `sherpa`.
  - If `sherpa-onnx` is installed but model paths are missing/unconfigured, it does **not** select sherpa; instead, it falls back to `resemblyzer` if available.
  - If neither `sherpa` (with valid models) nor `resemblyzer` is available, raises `RuntimeError` with actionable instructions.
- **Availability Check:** Updated `check_backend_available(config: DiarizationConfig | None = None)` to verify model existence when config specifies the sherpa backend, while maintaining backwards compatibility when called without arguments.

### 3.2 `chomp/diarization/__main__.py`
- **CLI Arguments:** Added `--segmentation-model` and `--embedding-model` to `argparse`:
  - `--segmentation-model`: Path to sherpa-onnx segmentation ONNX model file.
  - `--embedding-model`: Path to sherpa-onnx speaker embedding ONNX model file.
- **Config Construction:** Passes `args.segmentation_model` and `args.embedding_model` into `DiarizationConfig`.
- **User Feedback:** Provides clear errors on missing model arguments when `--backend sherpa` is selected.

### 3.3 `tests/test_diarization.py`
Added 17 new tests (total 103 tests, all passing):
- `TestDiarizationConfig`:
  - `test_defaults`: Verifies `segmentation_model is None` and `embedding_model is None`.
  - `test_custom`: Verifies custom model paths on `DiarizationConfig`.
- `TestBackendAvailability`:
  - `test_sherpa_config_requires_model_files`: Tests `check_backend_available(config)` with missing models, non-existent files, and valid files.
- `TestDiarizeSherpaMocked`:
  - `test_sherpa_pipeline_mocked`: Verifies end-to-end execution with configured dummy model files and asserts paths reach constructors.
  - `test_sherpa_model_paths_reach_constructor`: Explicitly verifies `mock_sherpa.OfflineSpeakerSegmentationPyannoteModelConfig.assert_called_with(model=...)` and `SpeakerEmbeddingExtractorConfig.assert_called_with(model=..., num_threads=2)`.
  - `test_sherpa_missing_model_config_raises`: Tests `ValueError` when models are omitted.
  - `test_sherpa_missing_segmentation_config_raises`: Tests `ValueError` when segmentation model is omitted.
  - `test_sherpa_missing_embedding_config_raises`: Tests `ValueError` when embedding model is omitted.
  - `test_sherpa_invalid_segmentation_path_raises`: Tests `FileNotFoundError` when segmentation model file does not exist.
  - `test_sherpa_invalid_embedding_path_raises`: Tests `FileNotFoundError` when embedding model file does not exist.
  - `test_sherpa_both_models_missing_raises`: Tests `FileNotFoundError` when both model files do not exist.
  - `test_auto_backend_does_not_select_unconfigured_sherpa`: Confirms `backend="auto"` falls back to `resemblyzer` when sherpa is installed but models are unconfigured.
  - `test_auto_backend_selects_sherpa_when_models_present`: Confirms `backend="auto"` selects `sherpa_onnx` when valid model files exist.
  - `test_auto_backend_fails_gracefully_when_sherpa_installed_without_models_and_no_fallback`: Confirms clear error when no fallback is installed.
- `TestDiarizationCLI`:
  - `test_cli_passes_model_paths_to_config`: Tests CLI argument parsing for `--segmentation-model` and `--embedding-model`.
  - `test_cli_sherpa_unconfigured_fails`: Tests CLI exit code 1 when sherpa is requested without model files.

---

## 4. Verification Results

### 4.1 Test Suite Run
```powershell
py -m pytest -ra
```
Output:
```text
============================= test session starts =============================
platform win32 -- Python 3.14.6, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\Prajwal Sitoula\.gemini\antigravity\scratch\chomp-chomp
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.1
collected 103 items

tests\test_core.py ..............                                        [ 13%]
tests\test_diarization.py .............................................. [ 58%]
..................................                                       [ 91%]
tests\test_pipeline.py .........                                         [100%]

======================= 103 passed, 1 warning in 4.86s ========================
```

### 4.2 CLI Verification
```powershell
py -m chomp.diarization --help
```
Output:
```text
usage: python -m chomp.diarization [-h] --media MEDIA [--output OUTPUT]
                                   [--num-speakers NUM_SPEAKERS]
                                   [--backend {auto,sherpa,resemblyzer}]
                                   [--audio-stream AUDIO_STREAM]
                                   [--min-segment-ms MIN_SEGMENT_MS]
                                   [--confidence-threshold CONFIDENCE_THRESHOLD]
                                   [--segmentation-model SEGMENTATION_MODEL]
                                   [--embedding-model EMBEDDING_MODEL]
                                   [--verbose]

Extract anonymous speaker turns from gaming/livestream VODs

options:
  -h, --help            show this help message and exit
  --media MEDIA         Input media file (video or audio)
  --output OUTPUT       Output directory for diarization artifacts
  --num-speakers NUM_SPEAKERS
                        Expected number of speakers (omit for automatic
                        detection, 2-6)
  --backend {auto,sherpa,resemblyzer}
                        Diarization backend (default: auto)
  --audio-stream AUDIO_STREAM
                        Audio stream index to extract (0-based)
  --min-segment-ms MIN_SEGMENT_MS
                        Minimum speech segment length in ms
  --confidence-threshold CONFIDENCE_THRESHOLD
                        Confidence threshold for speaker clustering
  --segmentation-model SEGMENTATION_MODEL
                        Path to sherpa-onnx segmentation ONNX model file
  --embedding-model EMBEDDING_MODEL
                        Path to sherpa-onnx speaker embedding ONNX model file
  --verbose, -v         Enable verbose logging
```

### 4.3 Clean Package Check
```powershell
py -m compileall -q chomp tests
py -m pip check
```
Output:
```text
No broken requirements found.
```

---

## 5. Limitations & Notes

1. **Mocked CI vs. Real Model Execution:** Per requirements, automated tests use mocked `sherpa-onnx` calls with synthetic audio and dummy model files to prevent large model downloads during CI. Real-world execution requires manually downloading `model.onnx` (segmentation) and an embedding model (`.onnx`) from the official sherpa-onnx GitHub releases.
2. **File Formats:** Model files must be in ONNX format compatible with the installed `sherpa-onnx` runtime.
3. **No Redesign:** The pipeline, models, and core diarization flow were preserved without architectural modifications.
