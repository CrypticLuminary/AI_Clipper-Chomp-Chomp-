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
| `INTEG-09` **Bridge** | Combine approved Stage 1 modules and cross-component tests | `SOL61-WORK` **(assigned)** | `integration/integ09-sol61-work-bridge` **(not created)** | GPT-6.1 Sol / High selected; draft integration PR only, no merge to main |

**Model codes:** `GPT6-REVIEW` = GPT-6 review/coordinator chat; `GPT6-DEV` = other GPT-6 coding chat; `OPUS46-AG` = Claude Opus 4.6 Thinking in Antigravity; `GFLASH38-AG` = Gemini 3.8 Flash in Antigravity; `ARGON-GEM` = Argon in Gemini app; `GLM53` = GLM 5.3; `SOL61-WORK` = GPT-6.1 Sol running the integration task in ChatGPT Work (planned, subject to model availability). These are internal labels, not independently verified claims of model authorship.

**Rule for future branches:** `<TASK-CODE> / <MODEL-CODE>` must appear in the task manifest and PR title; branches follow `feat/<task-code-lower>-<model-code-lower>-description` where practical. Each agent edits *its own* `docs/tasks/<TASK-CODE>.md`, not this shared table; the reviewer updates this README after verifying provenance and integration. See [agent attribution rules](docs/agent-task-registry.md).

---

Chomp-Chomp finds possible highlights in authorized completed livestream recordings and prepares up to three distinct vertical clips for human review.

Install Python 3.11+ and FFmpeg with libass. On Windows, create a virtual environment and install the project:

~~~powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[facecam]'
~~~

Run on a local recording with its timestamped transcript:

~~~powershell
chomp --input 'C:\VODs\recording.mp4' --transcript 'C:\VODs\transcript.json' --rights-ref creator-grant --acknowledge-permission --render
~~~

Open the review page printed by the command. An empty result is valid. The candidate ledger records the proposals and selection decisions. Use the editable plan to correct a crop or the clip interval, then rerender and review that revision. Publishing remains manual.

The default Shorts layout detects and tracks webcam containers and focuses the gameplay panel on the visible action. See the [Shorts manual](docs/shorts-layout.md) for model setup, camera slots, titles, and the final export profile.

See the [Stage 1 workflow](docs/stage1-quality.md) for local Whisper and semantic model setup, temporal layouts, caption corrections, final exports, and the benchmark. The [architecture proposal](docs/architecture-v1.md) describes the later roadmap.

The project can keep the default pipeline on the machine. Optional local model and detector weights are installed separately. Human editorial quality, timing, and framing are evaluated on real footage in addition to the offline tests.
