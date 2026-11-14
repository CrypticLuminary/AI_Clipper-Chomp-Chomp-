# Chomp-Chomp 🐊

**Local-first, review-gated highlight discovery for authorized completed livestream recordings.**

Chomp-Chomp is being developed in stages: (1) audio-first VOD analysis, (2) multiple VODs, (3) continuous livestream capture. We deliberately separate broad candidate discovery from semantic understanding, quality ranking, video editing and posting.

> **Current status — v0.1.0 early baseline:** works with local video files. Uses FFmpeg to detect audio deviations, optional JSON transcripts and replay chat, configurable *local* Ollama interpretation, rank-first overlap removal, up to three review candidates, and optional letterboxed vertical MP4 preview exports. It **does not predict virality**, automatically discover all compelling moments, guarantee accurate reaction categories, retrieve remote stream links, detect facecams, or post to social platforms. ASR is optional, not installed by default.

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

You can supply `--ollama-model MODEL_NAME` if you have a local Ollama model running on `127.0.0.1:11434`. This attempts a **semantic** quality assessment of candidates. Without it, ranking is an explicit **heuristic baseline**, not an AI judgment. LLM failures revert to heuristics and are recorded in `warnings`.

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

Each source gets an independent `outputs/<job_id>/review_queue.json`, optional `candidate_01.mp4` and so on. A SQLite `outputs/jobs.sqlite` tracks job states. All outputs are **pending human review** and publishing is disabled. The current MP4 preview keeps full source visible with padding; it does not pretend to detect facecams, and it isn't yet a fully branded campaign-ready cut.

Prioritization is provisional: a score is **not** a probability of future views. Audio spikes may be game effects, punctuation may not reflect excitement, and chat bursts may represent technical issues. Source-relative millisecond timestamps are used throughout. The same source with modified content can receive a different local job identity. Cache/provenance and crash recovery will be hardened before scalable batch execution.

## Roadmap

- M0: typed source clocks, audio + transcript + chat inputs, baseline rank/select, review-only exports, regression tests. **Implemented in this branch.**
- M1: benchmarked ASR, transcript chunking and semantic ranker with evidence validation; honest context/boundary expansion.
- M2: selective VOD video retrieval for permitted platform sources; accurate facecam/gameplay vertical layouts, captions, edit manifests, technical verification.
- M3: matched real-footage experiments comparing audio + transcript against optional chat; human precision@3.
- M4: reliable bounded batch scheduler and stage cache.
- M5: separate authorized paid-campaign trial with manual submission and tracked actual approvals/payouts.
- M6: sparse visual scouting if it yields demonstrable recall improvement.
- M7: live capture adapter and causal scoring.

The detailed agreed [architecture](docs/architecture-v1.md) is a **design proposal**, not a claim about implemented features. Any borrowed code or model weights need independent commercial-license review. Don't submit clips without documented streamer and campaign rights.
