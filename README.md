# Chomp-Chomp 🐊

## Agent / task registry (Stage 1)

> **Task and model codenames are project labels, not GitHub identities.** Existing branch authorship cannot be proven from Git commits; unconfirmed entries stay `UNVERIFIED` until the operator confirms them. New agents must self-report their model, app, task ID, branch and PR in a dedicated task manifest.

| Task code | Section | Model / agent code | Working branch | Review state |
| --- | --- | --- | --- | --- |
| `GATE-01` **Watchtower** | CI and testing | `GPT6-REVIEW` | `feat/vod-foundation` | Automated CI passed |
| `FRAME-02` **Portrait** | Video layouts and media export | `GPT6-REVIEW` | `feat/vod-foundation` | Implemented; real-footage review pending |
| `VOICE-03` **EchoMap** | Diarization and speaker IDs | **`UNVERIFIED`** (original branch) | `feat/stage1-speaker-diarization` | Parent PR missing; 3 fixes to integrate; real backend unverified |
| `SCOUT-04` **Radar** | Broad highlight discovery | `ARGON-GEM` **(assigned, unverified)** | `feat/stage1-discovery` **(not confirmed created)** | Awaiting implementation/review |
| `JUDGE-05` **Lens** | Semantic highlight interpretation and ranking | `GLM53` **(planned)** | Not created | Deferred; no API integration |
| `CUT-06` **Cutline** | Clip boundary refinement | `GPT6-DEV` **(user-reported GPT chat; model unverified)** | `feat/stage1-clip-boundaries` | PR #2 CI 4/4; technical pass, real-video review pending |
| `EVAL-07` **ClipLedger** | Human clip-quality review and evaluation | `GPT6-DEV` **(self-reported)** | `feat/eval07-gpt6-dev-clipledger` | PR #3 CI 4/4; technical pass, human VOD review pending |
| `VOICE-03-FIX01` **ModelPaths** | Sherpa model loading fix | `GFLASH38-AG` **(self-reported)** | `fix/voice03-fix01-gflash38-ag-modelpaths` | PR #4 CI 4/4; real ONNX model smoke test pending |
| `VOICE-03-FIX02` **IdentityGuard** | Protect speaker identity mappings | `GFLASH38-AG` **(self-reported)** | `fix/voice03-fix02-gflash38-ag-identityguard` | PR #5 CI 4/4; technical pass, integrate FIX03 before parent |
| `VOICE-03-FIX03` **TurnsGuard** | Protect speaker-turn exports from overwrite | `GFLASH38-AG` **(self-reported)** | `fix/voice03-fix03-gflash38-ag-turnsguard` | PR #7 CI 4/4; technical pass, shared WAV concurrency caveat |
| `CAPT-08` **CaptionCraft** | Caption timing and readability | `GPT6-DEV` **(self-reported)** | `feat/capt08-gpt6-dev-captioncraft` | PR #6 CI 4/4; technical pass, real subtitle review pending |
| `INTEG-09` **Bridge** | Combine approved Stage 1 modules and cross-component tests | `ASTRA-WORK` **(assigned)** | `integration/integ09-astra-work-bridge` **(not created)** | Assignment only; integrate in draft PR, no merge to main |

**Model codes:** `GPT6-REVIEW` = GPT-6 review/coordinator chat; `GPT6-DEV` = other GPT-6 coding chat; `OPUS46-AG` = Claude Opus 4.6 Thinking in Antigravity; `GFLASH38-AG` = Gemini 3.8 Flash in Antigravity; `ARGON-GEM` = Argon in Gemini app; `GLM53` = GLM 5.3; `ASTRA-WORK` = GPT-6 Astra running the integration task in ChatGPT Work (planned, subject to model availability). These are internal labels, not independently verified claims of model authorship.

**Rule for future branches:** `<TASK-CODE> / <MODEL-CODE>` must appear in the task manifest and PR title; branches follow `feat/<task-code-lower>-<model-code-lower>-description` where practical. Each agent edits *its own* `docs/tasks/<TASK-CODE>.md`, not this shared table; the reviewer updates this README after verifying provenance and integration. See [agent attribution rules](docs/agent-task-registry.md).

---

**Review-gated highlight discovery and export for authorized completed livestream recordings.**

Chomp-Chomp is being developed in stages: (1) audio-first VOD analysis, (2) multiple VODs, (3) continuous livestream capture. We deliberately separate broad candidate discovery from semantic understanding, quality ranking, video editing and posting.

> **Current status — v0.3.0 early baseline:** accepts **local video recordings**, finds provisional highlights using FFmpeg audio peaks and optional transcripts/chat, ranks them with an *explicit heuristic baseline*, refines provisional boundaries using transcript segments, and selects up to three review candidates. With `--render` it exports 540×960 H.264/AAC videos, segment-timed SRT captions (burned in when present), and versioned edit specifications. A supplied facecam ROI enables a fixed facecam/gameplay split; without one, the full source remains visible with padding. **No LLM, Meta/GPT/Claude API, or agent integration is active yet.** Optional local ASR is not a language-model ranking service. No stream-URL fetching or automatic posting is implemented.

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

## Speaker identity (multiplayer/Discord/voice chat)

The first real-world test showed why we must distinguish the main streamer from teammates, other spoken voices, and viewer text chat. **Whisper does not diarize or identify the main streamer**. Chomp-Chomp v0.3 supports speaker labels supplied by an external diarizer or a human, and stores a per-candidate speaker timeline. This is *not yet* automatic diarization or microphone source separation.

Provide an existing labelled transcript: 

```json
{"segments": [{"start": 8.1, "end": 10.5, "speaker_id": "SPEAKER_00", "text": "Wait, did you see that?"}, {"start": 10.5, "end": 12.0, "speaker_id": "SPEAKER_01", "text": "No way!"}]}
```

Or provide a separate diarization file such as `speaker_turns.json` alongside ordinary ASR:

```json
{"turns": [{"start": 8.0, "end": 10.5, "speaker_id": "SPEAKER_00"}, {"start": 10.5, "end": 12.2, "speaker_id": "SPEAKER_01"}]}
```

After **listening to the recording and confirming** which anonymous voice is the streamer:

```powershell
chomp --input test.mp4 --rights-ref creator-consent --acknowledge-permission --transcribe --speaker-turns speaker_turns.json --main-speaker-id SPEAKER_00 --render
```

Candidate records include `speaker_context` with times, text, anonymous speaker IDs and explicit `main_streamer`, `other_speaker` or `unverified` roles. If speech crosses two voices, the segment remains unverified rather than guessing. The `--main-speaker-id` option rejects an ID that isn't present. **Speaker roles do not yet affect heuristic ranking**: teammates can deliver the payoff to a funny exchange. Chat messages are a different input from spoken voices. Next: an opt-in tested diarization/voice-reference stage or capture of separate mic tracks, with explicit permission and confidence thresholds.

## Section 2: two vertical video formats and exact manual previews

Chomp-Chomp now has **two primary 9:16 editing formats**:

1. `facecam-gameplay`: the streamer webcam fills the top 35%, with gameplay underneath. **You must provide the webcam rectangle** using source-pixel coordinates (automatic facecam detection does not exist yet).
2. `gameplay-only`: center-filled vertical gameplay footage without a separate facecam. This intentionally crops the left and right edges of 16:9 footage. Optionally use `--gameplay-roi` to choose the important region.

`--layout auto` uses the first format if `--facecam-roi` is provided, otherwise the second. `--layout letterbox` remains available as a diagnostic legacy layout but is not a recommended social-video format. Since many streams have different webcam positions, use a frame from the original video to measure the webcam rectangle; do **not** blindly reuse the example coordinates.

**Test an exact interval without the provisional highlight selector** (15 seconds, for example):

```powershell
chomp --input "test.mp4" --rights-ref "creator-permission" --acknowledge-permission --render --start 780 --end 795 --layout facecam-gameplay --facecam-roi "20,25,420,250"
```

Or gameplay-only:

```powershell
chomp --input "test.mp4" --rights-ref "creator-permission" --acknowledge-permission --render --start 780 --end 795 --layout gameplay-only
```

Replace timestamp/ROI values with real source locations. For game footage near the edge of the original frame, pass a separate `--gameplay-roi "x,y,w,h"`. Both modes use AAC stereo at 48 kHz; select an alternative audio track with `--audio-stream 1` (zero-based audio-track index) when the first track isn't the intended mix. Render output is saved under `outputs/manual-.../manual_preview.mp4` with a JSON report and optional SRT if a timestamped `--transcript` file is supplied. Manual mode deliberately bypasses semantic ranking; it doesn't automatically identify whether the segment is entertaining.

Ordinary automatic runs also use the selected layout:

```powershell
chomp --input "test.mp4" --rights-ref "creator-permission" --acknowledge-permission --transcribe --render --layout gameplay-only
```

**Limitations:** webcam coordinates must be manually confirmed; gameplay-only center cropping may cut off HUD/important action, and speaker/source separation and semantic moment selection remain future features. No automatic upload or model/API calls are introduced by the layout options.

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
- M0.7: external speaker diarization labels and streamer-role contracts. **Implemented (v0.3; identification is manual, no automatic diarization yet).**
- M1: benchmarked ASR, transcript chunking, and model-agnostic semantic ranker with evidence validation; honest setup/payoff recovery. The ranker can later use authorized Meta/GPT/Claude APIs or agent workflows.
- M2: selective VOD video retrieval for permitted platform sources; automatic/corrected facecam regions, word-level captions, campaign profiles, expanded technical verification.
- M3: matched real-footage experiments comparing audio + transcript against optional chat; human precision@3.
- M4: reliable bounded batch scheduler and stage cache.
- M5: separate authorized paid-campaign trial with manual submission and tracked actual approvals/payouts.
- M6: sparse visual scouting if it yields demonstrable recall improvement.
- M7: live capture adapter and causal scoring.

The detailed agreed [architecture](docs/architecture-v1.md) is a **design proposal**, not a claim about implemented features. Any borrowed code or model weights need independent commercial-license review. Don't submit clips without documented streamer and campaign rights.

## What v0.2.1 repairs (quality/diagnostics)

- Overlap suppression compares the shared interval with the shorter clip. Large repeats no longer pass because they have low intersection-over-union.
- Review subtitles are broken into shorter, more readable cues and use a smaller style; still **segment-estimated**, not word-aligned.
- Exports preserve stereo AAC audio at 48 kHz and report decoded mean audio dBFS in `review_queue.json`. A stream may be present but silent; this diagnostic checks actual samples.
- The default vertical layout is still letterboxed. Use a correct source-specific `--facecam-roi` to get a preliminary split layout. Proper scene-aware edits need further development.
- There is **no semantic AI ranker yet**, so v0.2.1 remains a technical prototype, not a reliable entertainment selector.

If you cannot hear audio in a preview, first open the exported MP4 in VLC or another local player. To isolate audio playback on Windows, run `ffplay -nodisp -autoexit "outputs\<job_id>\candidate_01.mp4"` (replace `<job_id>` with the actual folder). A non-silent audio level in the manifest alongside local playback silence suggests a player, application, or output-device issue.

## Real-video feedback: audio, duplicates and subtitle quality

- Up to three clips are selected after duplicate suppression based on overlap with the *shorter* interval (not a loose union ratio); distinct moments should not reuse 20 seconds of the same scene.
- Source speech captions are split into shorter cues and rendered at a more restrained font size. This is still approximate segment timing, not word alignment.
- Exports use AAC stereo 48 kHz with audio decode/level diagnostics recorded as `audio_mean_dbfs` for every rendered clip. This checks that an AAC stream contains audible signal (but cannot test your sound device).
- The unconfigured layout is still full-frame letterbox; it is a review preview, not a final competitive social-video edit.
- **Poor candidate choice remains expected without a semantic model or agent.** Speaker tags are preserved for future AI interpretation but do not yet magically improve ranking.

If an MP4 plays silently, test the file outside the chat preview in VLC or run `ffplay -nodisp -autoexit "outputs\YOUR_JOB_ID\candidate_01.mp4"`. Compare `audio_mean_dbfs` with -60 dBFS: a value near -24 dBFS confirms the export contains sound, even if a specific player/device is muted.
