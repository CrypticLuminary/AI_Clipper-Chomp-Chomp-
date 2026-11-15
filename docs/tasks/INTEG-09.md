# INTEG-09 — Bridge

- **Agent** `SOL61-WORK`
- **Integration branch** `integration/integ09-sol61-work-bridge`
- **Target** `feat/vod-foundation` at `c8718a1ef856c1f634feafbb6fe47b79548b95bc`
- **Local worktree** `chomp-chomp/integ09-bridge`, separate from the existing checkout at `37d8ff2d185fa4410ea5dd2097e2e1684923b23d`

## Initial audit

Fetched the base and seven assigned source refs on 15 November 2026. The base advanced by 11 documentation commits after the local checkout. PRs #2 through #7 were open drafts and unmerged. Their heads matched the fetched branches. The base and each assigned head had four successful Windows/Ubuntu × Python 3.11/3.13 jobs in the linked runs.

| Component | Fetched branch | Pinned head | Merge base for this component | Original CI |
| --- | --- | --- | --- | --- |
| Foundation | `feat/vod-foundation` | `c8718a1ef856c1f634feafbb6fe47b79548b95bc` | — | [43666026228](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/actions/runs/43666026228) |
| Cutline [#2](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/2) | `feat/stage1-clip-boundaries` | `82ad6d4dd61d8113c1dbfc9d774eed80203d00fa` | `37d8ff2d185fa4410ea5dd2097e2e1684923b23d` | [43603837132](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/actions/runs/43603837132) |
| CaptionCraft [#6](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/6) | `feat/capt08-gpt6-dev-captioncraft` | `515c2d9d940ed698e6510d148a889654caa93af0` | `9d361ab0f7afdede21795535dede71cece97bae4` | [43613150202](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/actions/runs/43613150202) |
| ClipLedger [#3](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/3) | `feat/eval07-gpt6-dev-clipledger` | `8989333d9f88dc4485467ff393772a864f2a7108` | `c043890557f6b1b7911cb761a316deb35e8292ae` | [43606910564](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/actions/runs/43606910564) |
| EchoMap | `feat/stage1-speaker-diarization` | `804a28c09410ddc088f8e4cf18f8342b848ec408` | `37d8ff2d185fa4410ea5dd2097e2e1684923b23d` | [43603210295](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/actions/runs/43603210295) |
| ModelPaths [#4](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/4) | `fix/voice03-fix01-gflash38-ag-modelpaths` | `d713e7532e8cf8f3a2e22f840c2a178db9c7b9e3` | `804a28c09410ddc088f8e4cf18f8342b848ec408` | [43611860711](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/actions/runs/43611860711) |
| IdentityGuard [#5](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/5) | `fix/voice03-fix02-gflash38-ag-identityguard` | `4f5b78b4aafdfa61b7b657638dedc92895528e13` | `804a28c09410ddc088f8e4cf18f8342b848ec408` | [43612354512](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/actions/runs/43612354512) |
| TurnsGuard [#7](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/7) | `fix/voice03-fix03-gflash38-ag-turnsguard` | `ba300264d949baefd1cc4b1599e1c7f3de2acfc5` | `4f5b78b4aafdfa61b7b657638dedc92895528e13` | [43613825728](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/actions/runs/43613825728) |

Cutline changes `chomp/boundaries.py`, CaptionCraft changes `chomp/captions.py`, and ClipLedger adds an evaluation package. The three components have no overlapping implementation files. EchoMap introduces `chomp/diarization/` plus optional dependencies. ModelPaths edits the backend configuration and CLI. IdentityGuard edits identity saving and export. TurnsGuard extends the same export path and is a direct descendant of IdentityGuard. The seven `git merge --no-ff` operations ran in table order. Each follow-up merge imported only commits absent at that point. SCOUT-04 / Radar and JUDGE-05 / Lens are outside this integration.

## Merge and component verification

All seven merges applied without textual conflicts. After each merge, checked the parent diff, matched changed paths to the pinned source files, and ran the named component tests. No upstream tests were removed or edited to make the suite pass.

| Component | Integration merge | Local suite | Result |
| --- | --- | --- | --- |
| Cutline | `394d0b8` | `test_boundaries_stage1.py`, `test_core.py` | 36 passed |
| CaptionCraft | `c12d3ce` | `test_captioncraft.py`, `test_pipeline.py` | 32 passed |
| ClipLedger | `89bf852` | `test_evaluation.py` | 53 passed |
| EchoMap | `cbc9c2d` | `test_diarization.py` | 67 passed |
| ModelPaths | `6f36d0e` | `test_diarization.py` | 80 passed |
| IdentityGuard | `560a5d6` | `test_diarization.py`, `test_diarization_export.py` | 87 passed |
| TurnsGuard | `17edce9` | `test_diarization.py`, `test_diarization_export.py` | 90 passed |

## Compatibility fixes

- `chomp/media.py`, `chomp/pipeline.py`, and `chomp/diarization/{core,integration}.py` preserve elapsed source time when decoding an audio track that starts after video. On the delayed-audio fixture the old WAV began with the tone and ended around 2.77 s for a 4 s source. Timestamp-aware resampling now preserves initial silence. Speaker-turn export defaults to the same FFprobe source duration as the VOD pipeline, keeping decoded AAC padding from extending exported turns past the source bounds. Cutline consumes the existing one-second audio scan from the requested track when it has no nearby transcript. The pipeline job key distinguishes the combined revision from earlier preview artifacts.
- `chomp/ranking.py` returns an empty set for a zero clip budget and rejects scores outside the finite 0–100 range. The default threshold, top-three selection, overlap suppression and human review statuses are unchanged.
- `chomp/diarization/core.py` tolerates absent NumPy during import so the standalone help and availability error work in a base install. ModelPaths still checks the two ONNX model files. The main Chomp and ClipLedger CLIs continue to work without diarization backends.
- `chomp/evaluation/core.py` accepts the v0.2 and v0.3 review queues after checking their shared source-clock fields against the historical emitters at `42d3420` and `98cc365`. The same strict count, timestamp, identity and source-clock validation applies. The renderer still emits v0.4. Existing queues and human label files stay intact.
- `tests/test_stage1_integration.py` exercises an actual rendered and decoded H.264/AAC preview, burned captions with silent gaps, Unicode SRT, CLI diarization-to-attribution round trip with a stubbed model, both overwrite guards, ClipLedger templates and reports, source clock comparison, zero output and rank-first duplicates. The delayed track, zero budget, invalid score and dependency-free help tests failed on the combined source before the compatibility fixes and passed afterward.

## Initial full verification

Windows 11 x64, Python 3.13.14, FFmpeg/FFprobe `9.0.2-full_build-www.gyan.dev`, pytest 9.2.0 and NumPy 2.5.4 in an isolated environment. Editable `.[dev]` installation used an offline wheelhouse. The first integrated `python -m pytest -q -ra` run passed 218 tests with no skips. `python -m compileall -q chomp tests`, `python -m pip check`, `chomp --help`, `python -m chomp.diarization --help`, and `python -m chomp.evaluation --help` passed. `python tests/bench_boundaries_stage1.py` also completed. The 70-candidate synthetic sample over four hours of timestamps took median 0.056 s before and 0.373 s with Cutline's additional checks. This excludes media decoding and provides no clip-quality claim.

## User-supplied clipper reference

Read `gta6-clipper-skill.zip` as source data. SHA-256 `d0a291f34fa80551f23cd46fae2ef395c4c9d0ab5a2c0c59b141e5287437193d`. The archive contains a macOS-oriented shell installer and actual Python modules. `scripts/clipper/fetch.py:42–74` implements audio-first downloads and selected video fetches. `scan.py:27–71,110–146` implements blocked ASR and YouTube chat replay bucketing. `moments.py:94–102,164–214` sends transcript evidence to Claude Code. `render.py:19–32,45–64` estimates alignment against downloaded audio, and `captions.py:69–122` builds ASS from a second ASR pass. `layout.py:33–75,150–189,212–290` samples faces and chooses a crop or full-frame fit. The archive has no license file. The implementation here uses independent code for a local framing technique. Archive code, credentials, models, installers and remote calls were not run or copied into the project.

- `chomp/framing.py` adds opt-in `--layout auto-facecam` for selected candidates and manual preview ranges. It samples up to 12 frames, uses recurrent small edge faces and local motion, and requires support across the clip. A likely camera shift or zoom in consecutive samples triggers `fit`, which preserves the full frame over a blurred background. A selected crop fits into the top panel and its evidence appears in the edit spec. An explicit ROI still takes precedence. `--layout auto` retains its previous behavior.
- `chomp/{cli,manual,media,pipeline}.py` routes both entry points through the optional framing decision. The unchanged review gate remains in force for every export. Facecam fingerprints distinguish model/runtime presets in the job cache. Captioned manual previews use a fresh revision key to preserve outputs produced by the earlier captioner. `pyproject.toml` adds the optional `[facecam]` extra, and `docs/stage1-media.md` describes use.
- `tests/test_auto_facecam.py` covers stable and animated edge faces, transient faces, multiple faces, a mid-clip camera zoom, manual overrides, a real split render, and a real full-frame fallback with both frame edges and non-silent audio present. The main CI regression has no model downloads. Real Windows inference used wheel `opencv-python-headless 4.14.0.94` and an explicitly supplied 232,589-byte YuNet ONNX file with SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`. [Pinned OpenCV Zoo model and MIT license](https://github.com/opencv/opencv_zoo/tree/f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet)

## Authorized VOD verification

Used the existing local `test.mp4` referenced by the prior `authorized-test` review queue, with its existing 82-segment ASR cache. The source is 1920×1080 H.264/AAC and 1,034.495 s. The pipeline proposed 30 candidates and selected one at the default heuristic threshold. Every preview decoded completely with `ffmpeg -xerror`; all four tested outputs were 540×960 H.264/AAC with clip-local cues and non-silent decoded audio. Inspected rendered frames against the source, including the enlarged webcam and frames during subtitle cues. All real labels remain blank, and the owner can review audio playback, natural-language synchronization and editorial suitability independently.

| Run | Source range | Result |
| --- | --- | --- |
| Ranked gameplay-only | 433.100–441.700 s | 8.600 s, 3 non-overlapping SRT cues, decoded RMS −27.25 dBFS and peak −9.49 dBFS. Baseline for the layout comparison. |
| Ranked automatic | 433.100–441.700 s | YuNet saw the small webcam in 9/12 samples and a temporary enlargement/displacement in the intervening samples. Full-frame fit preserved the expanded camera and game UI. Same duration, captions and audio levels as the gameplay-only output. |
| Exact-range automatic | 785.500–791.000 s | Stable face in 7/7 samples and detected source ROI `[1592,336,276,324]`. Top facecam and lower gameplay verified visually. 5.500 s, 1 truncated SRT cue, decoded RMS −30.33 dBFS and peak −11.64 dBFS. |
| Exact-range explicit ROI | 785.500–791.000 s | Source ROI `[1486,306,434,302]` confirmed from frames. Same 5.500 s clock, cue count and audio levels as the automatic split. |

The decoded audio envelope correlation with the corresponding source interval rounded to 1.0000 for the four exports. ClipLedger opened the two current queues and the pre-existing v0.2 queue. The legacy/new comparison matched one window with a zero-ms anchor offset, kept the original queue byte-for-byte, and reported precision as unknown. The sampled 8.6 s range spans a match transition and related team conversation. It still requires a human cut and payoff judgment. Haar fell back on the two sampled ranges in the first probe. The measured YuNet behavior is specific to this VOD.

## Final local verification

After framing and legacy-queue support, `python -m pytest -q -ra` passed **227 tests with no skips** on Windows/Python 3.13.14. `python -m compileall -q chomp tests` and `python -m pip check` passed. Both dependency sets were installed and checked using local wheels. The console and module help commands passed, including the simulated no-NumPy and no-model dependency case. `python -m chomp.evaluation.demo` generated its labeled synthetic fixture. Local VOD technical verification complete. Human content evaluation and real diarization model accuracy remain open.

## Known limitations

- Diarization keeps the fixed `diarization_input.wav` scratch filename. Two simultaneous exports to the same destination can still interfere during conversion or analysis. The JSON writes remain exclusive, and a late turns conflict may leave an unconfirmed identity template. Multi-file export is not transactional.
- Sherpa and Resemblyzer inference accuracy was not tested. A confirmed streamer ID still requires explicit operator input. For multi-track VODs, supply an aligned transcript from the intended audio track. The internal faster-whisper path still chooses its default audio track.
- CaptionCraft estimates timing from transcript segments and may shorten dense speech. Cutline uses context heuristics and requires the reviewer to judge setup and payoff. Ranking remains heuristic. ClipLedger's reviewed denominators cannot establish discovery recall or virality.
- Automatic facecam uses a fixed crop and sample-based evidence. It can miss fast or ambiguous changes, and a preferred streamer may be absent from the sample. A supplied ROI or `fit` provides a reviewable alternative. No identity or generalization claims follow from one VOD.
- ClipLedger compares matching asserted source identifiers and durations within one second. Its time-window pairs are potential same-event matches and need independent review for changed media or clocks.
- `main`, `feat/vod-foundation`, the source branches, and their PRs are outside the write scope. No remote acquisition, posting, database addition or new model-provider API is required.
