# DIR-01 — Director-first clip selection

- Milestone 1 implemented and locally verified on Windows. Independent playback and editorial review pending.
- Branch `feat/director-first` from `fix/fx03-transparent-cutout` at `bf0ff59`. The base contains integrated Stage 1, FX-01, FX-02, FX-03, and transparent-cutout work.
- Self-reported contributor `GPT6-DIR01`, Codex desktop on Windows PowerShell. Review is local.
- Paths include `chomp/director/`, the CLI dispatch, optional section offset in the shared audio scan, and Director tests and fixtures.

## Run in PowerShell

Install Python 3.11+ and FFmpeg with `ffprobe` available on PATH. These commands use `uv` for the environment. The Codex run uses your signed-in ChatGPT session and counts against its usage limits.

```powershell
Set-Location -LiteralPath 'D:\AI _Clipper\chomp-chomp\director-first'
uv venv --python 3.13
uv pip install -e '.[asr,director,dev,facecam]'
codex login status
# Run codex login if the CLI reports that you are signed out.
$chomp = '.\.venv\Scripts\chomp.exe'
$model = 'gpt-5.6-sol'
```

The test fixture uses Windows speech synthesis and FFmpeg. It contains a quiet library story, a high-amplitude interruption, and a timer story with a callback outside the 60-second clip maximum. The fixture transcript is authored with the audio timeline and supplied directly. The video shows a static background.

```powershell
$fixture = 'tests\fixtures\director-first'
$example = 'outputs\director-example'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$fixture\make-fixture.ps1" -Output $example
$opts = @("--campaign", "$fixture\campaign.json", "--transcript", "$fixture\transcript.json",
          "--output", $example, "--include-rejected", "--dry-run")
& $chomp clip "$example\fixture.mp4" --director codex --model $model --max-calls 8 @opts
& $chomp clip "$example\fixture.mp4" --director heuristic @opts
```

Open the `review_page` printed by either command. It contains playable source previews when the browser allows local media, source intervals, decision reasons, campaign checks, and cited transcript excerpts. The command also prints the path to `director.json`, which includes the campaign revision, window and gap coverage, candidates and decisions, investigation requests, per-call cache and usage data, and errors. The fixture hand-label uses `[10000,37200)` ms for the complete quiet story. The timer spans `[47500,147200)` ms.

For the Speed comparison, use the workspace's existing local VOD. Supply a valid rights reference for the recording. Install the local Whisper weights once if the model is missing.

```powershell
.\.venv\Scripts\python.exe -c "from faster_whisper.utils import download_model; download_model('small')"
$source = '..\AI_Clipper-Chomp-Chomp-\test3 (speed vod).mp4'
$brief = 'Find standalone funny conversations or unusual observations, including quieter dialogue. Prefer moments whose context and payoff are clear in words. Abstain if the payoff depends on visuals.'
$opts = @('--section', '2400', '2520', '--rights-ref', 'local-speed-editorial-evaluation',
          '--brief', $brief, '--output', 'outputs\speed', '--include-rejected', '--dry-run')
& $chomp clip $source --director codex --model $model --max-calls 8 @opts
& $chomp clip $source --director heuristic @opts
```

On macOS and Linux, activate the environment or invoke `.venv/bin/chomp` with the same options. FFmpeg, a local video and either local Whisper weights or an existing timestamped transcript are sufficient for source analysis. The bundled narration generator uses Windows.

## Campaign and evidence behavior

Start from [campaign.json](../../tests/fixtures/director-first/campaign.json). Explicit flags override the corresponding file settings. Duration and section flags accept seconds to millisecond precision. The clip count and minimum and maximum duration are gates. Target duration, preferred topics, platform, and aspect ratio guide editorial decisions. A duration-limit override moves the inherited target into the new permitted range when you leave `--duration` unset. Excluded phrases also gate selections through case- and punctuation-insensitive matching against the cut's transcript. Captions require available transcript evidence in the proposed cut. Human review is mandatory and all previews are pending review. A successful dry run can select zero clips.

The semantic pass reads timestamped speech in 90-second windows with 30 seconds of overlap and retains up to 12 recent evidence-linked notes across windows. Audio and optional chat-activity observations supply an independent source of leads. The heuristic mode uses the existing proposal and scoring functions as a baseline. The Codex pass may request up to four transcript ranges, with 60 seconds per read and 180 seconds total. Responses undergo schema and reference validation. Selection edges come from cited utterances and expand across overlapping speech before applying duration and section gates. A rejected interval can still be inspected in the report.

The Codex process runs with read-only permissions, approval requests disabled, and shell, web, apps and MCP tools disabled. It receives bounded transcript text and signal observations. Source video, raw audio, raw chat and rights references stay local. Each prompt has a 96,000-byte payload limit, each model call has a 150-second timeout, and the editorial phase has a 30-minute runtime budget. `--max-calls` caps new model calls per invocation. The report records CLI token counts when available. Subscription dollar cost is unknown. A budget or provider failure ends the current AI run with its recorded status and preserves completed calls. The job reuses validated call results by source, model, tool version, schema, prompt, and campaign context. Use the same output root to reuse local transcription and audio across brief and provider changes.

## Verification

Ran `.\.venv\Scripts\python.exe -m pytest -q` on Windows with Python 3.13.14. **323 tests passed.** The controlled tests verify quiet recall with empty signal input, memory across overlapping windows, retrieval of an empty range, hard duration and speech-overlap protection, schema and evidence rejection, partial-job resume, cache invalidation, and zero selections.

| Frozen input | Mode | Available / scanned utterances | Selected source spans | Run |
| --- | --- | --- | --- | --- |
| Synthetic fixture, 0–160 s | Codex | 6 / 6 | 10.000–37.200 s | 51.9 s, 4 calls, 47,233 input and 1,528 output tokens |
| Synthetic fixture, 0–160 s | Heuristic | 6 / baseline | 41.500–61.500 s and 18.750–38.750 s | Cached local audio and supplied transcript |
| Speed, 2,400–2,520 s | Codex | 24 / 24 | 2,403.250–2,445.370 s | 67.4 s, 4 calls, 47,595 input and 2,490 output tokens |
| Speed, 2,400–2,520 s | Heuristic | 24 / baseline | 2,466.050–2,495.090 s and 2,446.170–2,468.350 s | 2.8 s with cached local Whisper transcription |

The live runs used `gpt-5.6-sol` in Codex CLI `0.169.0-alpha.11.1`. A Speed rerun reused all four validated calls. A trial with `gpt-5.4-mini` surfaced the CLI's model-unavailable error. CLI token counts include any provider-side input caching reported by Codex. Timings reflect the local setup and cache state described here.

The synthetic library story peaked at −41.6 dBFS in the audio-analysis bins, compared with −5.7 dBFS for the interruption. Audio discovery produced no candidate anchored in the story. Codex kept its complete setup and payoff. The heuristic slice overlapping the story began after its setup.

The Speed run used cached local Whisper output from the frozen section. Codex requested a seven-second transcript read when assessing an incomplete closing thought. Its selected exchange starts in an ASR fragment about a rating and still needs playback to verify context. Quiet worthwhile moments, real-media recall and precision, invented-claim rates, and comparative editorial quality await human annotation. The synthetic checks and Speed dry run establish workflow behavior on those inputs.

Milestone 2 includes Claude and Meta provider parity, broader cost controls, and reproducible comparative scoring. The installed Claude CLI exposes `-p`, `--json-schema`, and JSON output, and was signed out during verification. Milestone 3 connects selections to validated export and the existing ShotComposer workflow. Milestone 4 adds interactive revision and richer evidence retrieval.
