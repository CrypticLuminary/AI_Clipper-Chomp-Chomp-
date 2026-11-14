# Chomp-Chomp 🐊

**Review-gated highlight discovery and export for authorized completed livestream recordings.**

Chomp-Chomp is being developed in stages: (1) audio-first VOD analysis, (2) multiple VODs, (3) continuous livestream capture. We deliberately separate broad candidate discovery from semantic understanding, quality ranking, video editing and posting.

> **Current status — v0.2.0 early baseline:** accepts **local video recordings**, finds provisional highlights using FFmpeg audio peaks and optional transcripts/chat, ranks them with an *explicit heuristic baseline*, refines provisional boundaries using transcript segments, and selects up to three review candidates. With `--render` it exports 540×960 H.264/AAC videos, segment-timed SRT captions (burned in when present), and versioned edit specifications. A supplied facecam ROI enables a fixed facecam/gameplay split; without one, the full source remains visible with padding. **No LLM, Meta/GPT/Claude API, or agent integration is active yet.** Optional local ASR is not a language-model ranking service. No stream-URL fetching or automatic posting is implemented.

## Windows quick start

Install [Python 3.11+](https://www.python.org/downloads/) and [FFmpeg](https://ffmpeg.org/download.html), including `ffmpeg` and `ffprobe` on PATH. Use a PowerShell terminal:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,asr]"
python -m pytest -q
```

Use a **local recording you own or are authorized to transform**:

```powershell
chomp --input "C:\path\authorized-recording.mp4" --rights-ref "permission-record-001" --acknowledge-permission --transcribe --asr-device cpu --render
```

For GPU ASR, try `--asr-device cuda` only after verifying compatible NVIDIA/CUDA runtimes. First run may download ASR model weights; `--asr-model` defaults to `small`. If ASR isn't installed, you can provide a timestamped transcript instead.

Offline/no-model evaluation with optional transcript and replay chat:

```powershell
chomp --input "C:\path\recording.mp4" --rights-ref "creator-grant-001" --acknowledge-permission --transcript transcript.json --chat chat.json --render
```

There is currently **no model-based semantic ranker**. API and external-agent adapters are reserved for a later step, so Chomp-Chomp does not depend on a desktop LLM. The current score is a heuristic for experimenting, **not** a viral-moment prediction.

## ASR compatibility troubleshooting

If transcription fails with `open() got an unexpected keyword argument 'metadata_errors'`, the installed PyAV version is incompatible with the current released `faster-whisper`. Until faster-whisper publishes a compatible release, Chomp-Chomp constrains the ASR extra to `av>=11,<19`. For an existing virtual environment, run:

```powershell
python -m pip install "av==18.1.0"
python -m pip check
chomp --input "test.mp4" --rights-ref "authorized-test" --acknowledge-permission --transcribe --render
```

Use an actual authorized local file and reference in place of these examples. This compatibility fix does not change AI-model weights or require a fresh transcription model download.

## Inputs

`transcript.json` (times in seconds or `start_ms`/`end_ms`):

```json
{"segments": [{"start": 5.0, "end": 7.0, "text": "Wait, what happened?"}]}
```

`chat.json` (source-relative seconds or `time_ms`):

```json
{"messages": [{"time": 6.0, "user_id": "pseudonym-1", "text": "clip that!"}]}
```

Chat is optional, and its absence is **not** a zero/negative quality signal. For misaligned replay chat, `--chat-offset-ms` applies a known correction. Invalid timestamps are rejected rather than silently clipped.

## Outputs and limitations

Each source gets an independent `outputs/<job_id>/review_queue.json`, `candidate_01.edit.json`, and, when a transcript is available, `candidate_01.srt`. With `--render`, it also generates a 540×960 captioned MP4. SQLite `outputs/jobs.sqlite` tracks job states. All exports are **pending human review** and publishing is disabled. Subtitle timings are based on source transcript segments; long segments are subdivided approximately and are **not word-level alignment**. If FFmpeg lacks the `subtitles` filter/libass, caption burn-in will fail clearly.

To use a known facecam region in source pixels, add `--facecam-roi 1200,50,450,260` (adjust to the recording). It produces a fixed facecam-on-top/gameplay-below layout. Omit the setting for a full-source letterbox. The program does not automatically find facecams yet.

Prioritization is provisional: a score is **not** a probability of future views. Audio spikes may be game effects, punctuation may not reflect excitement, and chat bursts may represent technical issues. Source-relative millisecond timestamps are used throughout. The same source with modified content can receive a different local job identity. Cache/provenance and crash recovery will be hardened before scalable batch execution.

## Roadmap

- M0: typed source clocks, audio + transcript + chat inputs, heuristic rank/select, review-only exports, regression tests. **Implemented.**
- M0.5: provisional transcript-assisted boundaries, segment-level SRT, optional fixed facecam/gameplay layout, 9:16 output and editable render specifications. **Implemented (v0.2).**
- M1: benchmarked ASR, transcript chunking, and model-agnostic semantic ranker with evidence validation; honest setup/payoff recovery. The ranker can later use authorized Meta/GPT/Claude APIs or agent workflows.
- M2: selective VOD video retrieval for permitted platform sources; automatic/corrected facecam regions, word-level captions, campaign profiles, expanded technical verification.
- M3: matched real-footage experiments comparing audio + transcript against optional chat; human precision@3.
- M4: reliable bounded batch scheduler and stage cache.
- M5: separate authorized paid-campaign trial with manual submission and tracked actual approvals/payouts.
- M6: sparse visual scouting if it yields demonstrable recall improvement.
- M7: live capture adapter and causal scoring.

The detailed agreed [architecture](docs/architecture-v1.md) is a **design proposal**, not a claim about implemented features. Any borrowed code or model weights need independent commercial-license review. Don't submit clips without documented streamer and campaign rights.
