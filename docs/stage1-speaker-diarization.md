# Stage 1 / Section 3 — Speaker Diarization and Identification

**Status:** Implemented optional diarization subsystem with dual backends (primary: `sherpa-onnx`, fallback: `resemblyzer`), conservative transcript attribution, explicit streamer identity mapping, and comprehensive mocked-model integration tests.  
**Date:** 2026-11-14  
**Primary target:** Windows 10/11 x64, Python 3.11–3.14, CPU-first local execution.  

---

## 1. Executive Summary

In gaming and livestream recordings (VODs), audio contains a complex mixture of:
- The main streamer's microphone voice
- Teammates/collaborators on voice chat (Discord, in-game VoIP)
- Game sounds, background music, explosions, and sound effects
- Pauses, coughs, shouts, and overlapping speech

Speech recognizers (such as Whisper / faster-whisper) transcribe *what* was spoken but **do not identify who spoke**. Furthermore, automated speaker diarizers output only anonymous cluster labels (e.g., `SPEAKER_00`, `SPEAKER_01`). 

### Core Architectural Principles
1. **Never assume `SPEAKER_00` is the streamer.** Anonymous cluster indices are arbitrary and depend solely on clustering order. Identifying the main streamer requires an explicit confirmation / mapping mechanism.
2. **Conservative attribution.** Rather than forcing a guess when game audio, music, or two people talk simultaneously, uncertain intervals are left unattributed (`speaker_id=None` or role `unverified`).
3. **Zero heavyweight model downloads in CI.** All test suites run fully offline using synthetic waveforms and mocked encoders; model weights are never fetched during automated CI runs.
4. **Optional dependencies.** Diarization dependencies are isolated in `[diarize]` and `[diarize-sherpa]` optional extras. The baseline pipeline works completely without them.

---

## 2. Technical Investigation & Comparison of Diarization Techniques

We evaluated seven approaches for local speaker diarization on Windows targeting gaming audio:

| Technique / Backend | License | Windows Native | Model Size | PyTorch Required? | HuggingFace Token? | Gaming Audio Suitability | Recommendation |
|---|---|---|---|---|---|---|---|
| **sherpa-onnx** (pyannote + CAM++) | **Apache 2.0** | **Yes** (pre-built wheels) | **~34 MB** | **No** (ONNX Runtime) | **No** (direct GitHub releases) | Good VAD & segmentation; clustering degrades on overlapping shouting | **Primary Recommended Backend** |
| **Resemblyzer** (GE2E encoder) | **Apache 2.0** | Yes | ~16 MB | Yes | No | Moderate on clean speech; sensitive to background game SFX | **Fallback Backend** |
| **pyannote.audio** 3.x | MIT (code & weights) | ⚠️ Difficult (WinError 1314 symlinks, sox_io missing) | ~600 MB | Yes | **Yes** (Gated model agreements required) | State-of-the-art on clean speech (~12% DER); 35–50% on chaotic gaming | ❌ Rejected (gated tokens, Windows friction) |
| **SpeechBrain** (ECAPA-TDNN) | Apache 2.0 | ⚠️ Moderate (torchaudio soundfile backend) | ~30 MB (model) + ~2 GB PyTorch | Yes | No (ungated models available) | Strong voice embeddings; requires custom VAD + clustering pipeline | ⚠️ Heavyweight (PyTorch CPU/GPU overhead) |
| **simple-diarizer** | **GPL-3.0** | ⚠️ Moderate | ~35 MB | Yes | No | Wraps SpeechBrain + Silero | ❌ Disqualified (GPL incompatible with MIT project) |
| **NVIDIA NeMo** | Apache 2.0 / OpenMDW | ❌ **No native Windows** (WSL2 required) | >500 MB | Yes | No | Enterprise accuracy; designed for Linux GPU servers | ❌ Rejected (No native Windows support) |
| **faster-whisper VAD + clustering** | Apache / BSD | Yes | 16–30 MB | Yes | No | Reuses Silero VAD; no overlapping voice handling | Subsumed into resemblyzer fallback |

### Technical Decision
- **Primary Backend: `sherpa-onnx`**
  - Permissive **Apache 2.0** license throughout.
  - Native Windows x64 support via ONNX Runtime without requiring PyTorch or CUDA toolkits.
  - Compact models (~34 MB total) for segmentation (PyAnnote-based ONNX port) and speaker embedding (CAM++).
  - No HuggingFace accounts, tokens, or gated terms of service required.
- **Fallback Backend: `resemblyzer` + `scikit-learn`**
  - Permissive **Apache 2.0** / BSD licenses.
  - Pretrained GE2E 256-dim embedding encoder (~16.3 MB).
  - Uses an energy-based VAD (built into Chomp-Chomp with zero extra dependencies) + cosine affinity spectral clustering.
- **Alternatives Rejected:**
  - *pyannote.audio*: Disqualified due to mandatory HuggingFace access tokens and gated user agreements, symlink permissions errors on Windows (`WinError 1314`), and complex dependency chains.
  - *simple-diarizer*: Disqualified due to viral GPL-3.0 license.
  - *NVIDIA NeMo*: Disqualified due to lack of native Windows support.

---

## 3. Subsystem Architecture

The diarization subsystem is isolated in `chomp/diarization/`:

```
chomp/
├── diarization/
│   ├── __init__.py         # Public API and exports
│   ├── __main__.py         # Standalone CLI entry point
│   ├── core.py             # Diarization engine (sherpa-onnx & resemblyzer backends, VAD, clustering)
│   ├── identity.py         # SpeakerIdentityMap and human confirmation contracts
│   └── integration.py      # Pipeline bridging and JSON export
```

### Module Responsibilities
- **`chomp.diarization.core`**:
  - Detects available backends dynamically (`check_backend_available()`).
  - Implements energy-based Voice Activity Detection (`_energy_vad`) with percentile-based noise floor estimation.
  - Loads and converts media to 16 kHz mono WAV with automatic channel downmixing and resampling.
  - Runs either `sherpa-onnx` or `resemblyzer` + `scikit-learn`.
  - Merges consecutive same-speaker turns separated by micro-pauses (<500 ms).
  - Validates and clamps all output intervals to integer source milliseconds.
- **`chomp.diarization.identity`**:
  - `SpeakerIdentityMap`: Manages the mapping from anonymous cluster IDs (`SPEAKER_00`, `SPEAKER_01`) to human-readable identities (`Streamer`, `Guest`).
  - Enforces `confirmed: bool` flag. Without explicit human confirmation, `is_main_speaker()` returns `False` and roles remain `unverified`.
  - Serialization to/from `speaker_identity_map.json`.
- **`chomp.diarization.integration`**:
  - `diarize_and_export()`: End-to-end extraction from video/audio to `speaker_turns.json` and `speaker_identity_map.json`.
  - `merge_diarization_with_transcript()`: Delegates to `chomp.speakers.attribute_segments` to maintain the conservative 65% single-speaker dominance and 45% margin rule.
- **`chomp.diarization.__main__`**:
  - CLI runner `python -m chomp.diarization --media VOD.mp4 --output outputs/diarize`.

---

## 4. Contract Specifications

### 4.1 Speaker Turns JSON Contract (`speaker_turns.json`)

The output adheres strictly to the existing `read_speaker_turns()` contract in `chomp/speakers.py`:

```json
{
  "turns": [
    {
      "start_ms": 1050,
      "end_ms": 4200,
      "speaker_id": "SPEAKER_00"
    },
    {
      "start_ms": 4800,
      "end_ms": 8900,
      "speaker_id": "SPEAKER_01"
    }
  ],
  "num_speakers_detected": 2,
  "method": "sherpa_onnx",
  "warnings": [],
  "metadata": {
    "backend": "sherpa-onnx"
  }
}
```

### 4.2 Speaker Identity Mapping Contract (`speaker_identity_map.json`)

```json
{
  "mappings": {
    "SPEAKER_00": "MainStreamer",
    "SPEAKER_01": "Teammate1"
  },
  "main_speaker_id": "SPEAKER_00",
  "confirmed": true,
  "confirmation_method": "manual_review",
  "notes": "Verified against facecam audio at 00:02:15"
}
```

---

## 5. Conservative Speech & Audio Handling

Gaming audio introduces unique failure modes that Chomp-Chomp handles conservatively:

1. **Silence:** Energy VAD identifies regions below the noise floor (`thresh = max(-35 dBFS, noise_floor + 8 dB)`). Silent periods produce no speaker turns, preventing spurious speaker labels during game loading screens or pauses.
2. **Overlapping Speech:** When two people talk simultaneously, embeddings become corrupted. In the transcript attribution stage (`chomp/speakers.py`), segments where no single speaker has ≥65% time dominance or where the difference between top speakers is <45% remain unattributed (`speaker_id=None`).
3. **Game Sound Effects & Music:** High-energy explosions or background music can corrupt voice embeddings. If clustering confidence (silhouette score) falls below `confidence_threshold` (default 0.25), a warning is logged and uncertain frames are tagged `SPEAKER_UNKNOWN`.
4. **Micro-pauses:** Natural speech contains 100–300 ms inter-word pauses. The merger collapses adjacent same-speaker intervals with gaps <500 ms into coherent turns.

---

## 6. Installation & Windows Setup Instructions

### 6.1 Prerequisites
- Windows 10 or 11 (64-bit)
- Python 3.11, 3.12, 3.13, or 3.14
- FFmpeg on PATH (with libass filter enabled)

### 6.2 Basic Installation (CI / Default Baseline)
Without diarization dependencies, the core pipeline and test suite run offline with mocked models:

```powershell
python -m pip install -e ".[dev]"
python -m pytest -ra
```

### 6.3 Installing Diarization Dependencies

#### Option A: Preferred Backend (`sherpa-onnx`)
```powershell
python -m pip install -e ".[diarize-sherpa]"
# or directly:
python -m pip install sherpa-onnx numpy
```

#### Option B: Resemblyzer Backend (Fallback)
```powershell
python -m pip install -e ".[diarize]"
# or directly:
python -m pip install resemblyzer scikit-learn numpy
```

---

## 7. Workflow & Existing Pipeline Integration

Because `chomp/pipeline.py` and `chomp/cli.py` are protected files under Section 3 ownership rules, integration is structured as a two-stage workflow:

### Step 1: Run Diarization on the Source Media
```powershell
python -m chomp.diarization `
  --media "C:\VODs\stream_20261114.mp4" `
  --output "outputs\diarization" `
  --num-speakers 2
```

This generates:
- `outputs\diarization\speaker_turns.json`
- `outputs\diarization\speaker_identity_map.json`

### Step 2: Human Review & Confirmation
Open `outputs\diarization\speaker_identity_map.json`, verify which speaker corresponds to the main streamer, and set:
```json
{
  "mappings": {
    "SPEAKER_00": "MyStreamerName",
    "SPEAKER_01": "DiscordFriend"
  },
  "main_speaker_id": "SPEAKER_00",
  "confirmed": true,
  "confirmation_method": "manual_review"
}
```

### Step 3: Run the Main Chomp-Chomp Pipeline
Pass the verified turns and confirmed streamer ID into the existing CLI:
```powershell
chomp `
  --input "C:\VODs\stream_20261114.mp4" `
  --speaker-turns "outputs\diarization\speaker_turns.json" `
  --main-speaker-id "SPEAKER_00" `
  --rights-ref "streamer-auth-001" `
  --acknowledge-permission `
  --render
```

The pipeline automatically:
1. Imports speaker turns via `read_speaker_turns()`.
2. Attributes transcript segments via `attribute_segments()`.
3. Validates the streamer ID via `speaker_inventory()`.
4. Attaches auditable speaker roles (`main_streamer`, `other_speaker`, `unverified`) to all discovered candidate clips via `candidate_speaker_context()`.

---

## 8. Proposed Future CLI Integration (Non-Breaking)

When ownership rules permit updating `chomp/cli.py` and `chomp/pipeline.py`, the following flag additions are recommended:

```python
# Proposed additions to chomp/cli.py:
p.add_argument("--diarize", action="store_true", help="run local speaker diarization on input audio")
p.add_argument("--diarize-backend", choices=["auto", "sherpa", "resemblyzer"], default="auto")
p.add_argument("--num-speakers", type=int, default=None, help="expected speaker count for diarization")
```

Under this integration:
1. If `--diarize` is passed and `--speaker-turns` is absent, the pipeline automatically calls `diarize_and_export()`.
2. The generated turns are cached under the job directory (`diarization-turns.json`).
3. If `--main-speaker-id` is omitted, all speakers are preserved as anonymous `SPEAKER_XX` and candidate context marks them as `unverified`.

---

## 9. Verification & Test Results

### 9.1 Test Suite Summary
The test suite in `tests/test_diarization.py` includes 26 test methods covering:
- **VAD correctness:** Clean tone detection, silence rejection, short-audio stability, empty input, ordered non-overlapping boundaries, `min_speech_ms` filtering.
- **Turn validation & cleanup:** Merging micro-pauses between same-speaker turns, duration clamping to source duration, zero-duration removal, sorting.
- **Identity mapping:** Human confirmation gating, validation warning triggers, JSON round-trip serialization, template generation.
- **Mocked pipeline tests:** Full end-to-end execution of both `sherpa-onnx` and `resemblyzer` backends without downloading models.
- **Edge cases:** Fallback to single speaker on sparse audio, silence producing zero turns, missing file detection, backend rejection when dependencies are missing, invalid backend argument rejection.
- **CLI verification:** Testing `python -m chomp.diarization` argument parsing, missing media errors, and graceful failure on missing backends.
- **Existing contract compatibility:** Ensuring diarization turns are 100% compatible with `read_speaker_turns`, `attribute_segments`, `speaker_inventory`, and `candidate_speaker_context`.

### 9.2 Measured Test Output
```
platform win32 -- Python 3.14.6, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\Prajwal Sitoula\.gemini\antigravity\scratch\chomp-chomp
configfile: pyproject.toml
testpaths: tests

tests\test_core.py ..............                                        [ 15%]
tests\test_diarization.py .............................................. [ 66%]
.....................                                                    [ 90%]
tests\test_pipeline.py .........                                         [100%]

======================== 90 passed, 1 warning in 4.62s ========================
```

---

## 10. Known Limitations & Failures

1. **Diarization Error Rate (DER) in Chaotic Gaming:** In high-intensity competitive gaming with simultaneous shouting, game gunshots, and loud electronic music, expect 30–50% DER compared to 10–15% in clean interview recordings.
2. **Multi-Track Advantage:** Diarization is an acoustic estimate. Creators using OBS multi-track recording (Track 1 = Master, Track 2 = Mic, Track 3 = Discord) should prefer `--audio-stream` isolation over single-track acoustic diarization whenever possible.
3. **No Automatic Streamer Inference:** Acoustic clustering cannot know which voice belongs to the streamer without facecam correlation or human confirmation.
4. **Quantization & CPU Latency:** On older CPUs without AVX2 instructions, embedding extraction for long 4-hour VODs can take 15–30 minutes. Run diarization on candidate clip intervals or downsampled audio when processing long recordings.
