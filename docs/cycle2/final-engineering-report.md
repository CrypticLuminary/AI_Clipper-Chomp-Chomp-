# KEYSTONE Cycle 2 engineering report

Date: 2026-11-25 (Asia/Katmandu). Tasks BASE-10, BENCH-11 and DIR-12. Actual agent attribution and environment are in `docs/tasks/BASE-10.md`; the runtime exposes Codex as GPT-6 based but no exact serving-model suffix. Historical replay attribution is distinct from this agent's attribution.

## 1. Executive summary and acceptance

Chomp-Chomp now has an opt-in deterministic route from validated Director First selections to review exports, a compatible ChompBench-1 evaluation reader, configurable provider phase budgets, multimodal research extension contracts and reproducible technical fixtures. A real VFR rendering defect was fixed. No new AI model, paid provider call, automatic publication or main-branch merge was introduced.

| Stage | Observed status |
|---|---|
| 0 repository/environment | Complete: fetched/audited remote heads, ancestry, source differences, open PRs and available CI metadata; isolated worktree prepared. |
| 1 reliable baseline | Technical gate passed: unmodified baseline 323/323 tests; reproducible exports and smoke matrix; VFR defect fixed; remaining composition limitation documented. |
| 2 ChompBench-1 | Technical gate passed: native and Director proposals, additive annotations, mismatch/malformed rejection, null handling, matched descriptors, synthetic JSON/Markdown reports. Full authorized/human 24-VOD dataset is unavailable. |
| 3 Director integration | Technical gate passed: fake-provider complete render; source/evidence/campaign/duplicate/zero/reject/abstain gates; selected track and caption checks; explicit failures/partial coverage; resume and changed-artifact rejection. |
| 4 research interfaces | Implemented/tested interfaces only. No semantic audio model, adaptive visual algorithm, calibrated fusion or non-dialogue semantic acceptance is claimed. |
| 5 real-video review | Partial: one previously authorized real 120-second source section, three new local review exports, six sampled source/output comparisons and decoded diagnostics. Independent playback/quality review and the other requested representative real scenarios remain pending. |
| 6 delivery | Coherent commits on work/cycle2-keystone; documents and handoff prepared. Remote draft/CI status is recorded in delivery-status.md. |

This is a completed engineering implementation with explicit unverified dataset/reviewer/research gates, not a claim that the entire real-world quality program is complete.

## 2. Original repository state and authoritative baseline

The supplied workspace root was a collection of worktrees rather than a Git root. Actual working repository: `D:/AI _Clipper/chomp-chomp/director-first`. It was clean on `feat/director-first` at `24bf2f5f4ad2172e4b18728b198fe7d3e9d65aff`. A new worktree was created at `D:/AI _Clipper/chomp-chomp/cycle2-keystone`, branch `work/cycle2-keystone`. Existing worktrees and unrelated local state were preserved.

All requested remote branches, including caption/evaluation/diarization fixes, are ancestors of the chosen baseline. Source/history comparisons show the earlier media, stable-camera, SubjectLock, ShotComposer and cutout improvements are already integrated. Baseline selection therefore follows actual integration ancestry and source differences, not commit count. `main` remains the initialization branch and was not merged into or changed. Open PRs #1–#11 were observed, including older already-integrated work. Director First had no PR-triggered CI run returned; no pass was inferred from an empty result. The combined-status query likewise returned no statuses.

See branch-audit.md for exact heads/common ancestors and module-map.md for source-derived public entry points, dependencies, workflow contracts and associated tests. Duplication is retained where experimental workflows need controlled comparison; downstream deterministic production uses the existing renderer rather than a rewritten editing engine.

## 3. Problems and root causes

1. VFR video could end early while audio continued. A synthetic source changing from 15 fps to sparse frames produced 3.733333 seconds of video for a four-second cut. The existing post-render guard rejected it. Legacy single-layout rendering lacked a realized frame grid; temporal code used average rather than nominal rate.
2. Director First ended at a dry-run format that the benchmark/production pipeline could not directly consume. Existing evaluation expected `chomp-candidates/1`; Director produced `director-selection/1`.
3. One shared provider cap allowed discovery to consume final-decision capacity. Cached results existed, but phase allocation and scan coverage needed explicit reporting and controls.
4. Title constraints diverged: Director accepted suggestions that the renderer rejected. A two-word synthetic suggestion initially blocked export despite valid boundaries.
5. The first synthetic registration pass exposed identical source bytes in multiple cases, including potentially different split labels. Distinct visual markers now make source identity explicit; generated registrations are validated before delivery.
6. On sampled real transparent-webcam footage, conservative automatic framing still preserves the full landscape source inside portrait output. This avoids inventing a webcam rectangle or choosing a competing game face, but does not meet the desired final portrait composition quality. It remains an explicit review/research issue.

## 4. Implemented changes and architecture

- `chomp/media.py`: nominal-frame-grid realization for VFR legacy renders; render cache revision 7 → 8; current CFR and native ShotComposer behavior preserved. Dedicated single/temporal regressions check both media-stream durations.
- `chomp/editorial_selection.py`: common source-aware format reader/Director adapter; preserves candidate identity, proposals/final intervals, select/reject/abstain, reasoning/story/confidence/suggestions, references, coverage, provenance, provider usage and review state. Missing scores remain null.
- `chomp/director/production.py`, `cli.py`, `director/clip.py`: opt-in exports through existing framing, manual.create_clip, captions, source audio, renderer, output validation and pending review. Actual source/transcript hashes, cited text/times, recomputed boundaries, campaign revision, exclusions/duplicates/counts and cached artifact hashes are checked. Reviewed layout overrides use existing validated geometry. The model cannot submit unrestricted rendering commands. Invalid title suggestions remain in editorial metadata and receive warnings while a usable cut is exported.
- `director/semantic.py`: separate configurable discovery/investigation capacity, final reserve, call/runtime timeout, phase/cached accounting, honest provider errors and partial scan ranges. Discovery algorithm and ordering were not redesigned.
- `evaluation/benchmark.py`, `__main__.py`, `experiments.py`: additive ChompBench-1 metadata, schema validation, creator split registration, native/Director evaluation, explicit denominators/nulls, technical sidecars, JSON/Markdown and matched A/B/C/D descriptors. Existing ClipLedger/layout/SubjectLock evaluators remain available.
- `research.py`: optional audio protocol, stable evidence IDs/provenance, simultaneous audio labels, selected-track identity, explicit unavailable states, bounded actual-PTS frame retrieval/cache/protected-box interface and lane-preserving candidate collection. No models/frameworks are imported or downloaded by these interfaces.
- `evaluation/smoke.py`, `technical.py`, `tests/test_cycle2*.py`, `tools/cycle2_audit.py`, `tools/cycle2_local_review.py`: reproducible synthetic fixtures, decoding/waveform diagnostics, cache-only private replay and regression checks. CI now installs the existing optional facecam extra and runs the complete synthetic matrix on Windows/Ubuntu Python 3.11/3.13.

The common interface reuses existing report dictionaries, Campaign, Candidate, TranscriptIndex/enforce, layout contracts and render APIs. There is no duplicate parallel set of editorial data classes. Legacy source-event span names remain intact; stricter ChompBench metadata is additive. Research evidence has a separate explicitly documented envelope to avoid breaking existing Director/ShotComposer schemas.

## 5. Exact tests and measurements

Baseline commit: `24bf2f5f4ad2172e4b18728b198fe7d3e9d65aff`. Baseline command `.venv/Scripts/python.exe -m pytest -q -ra`: **323 passed in 51.03 s**, zero failures/skips.

Final code checkpoint: `734aa37e1c8ba8d677869e6907b182e36cf67147`. Same command: **355 passed in 49.12 s**, zero failures/skips. Added tests cover malformed adapters, source/track/evidence/interval mismatch, AI-completeness rejection, creator leakage, phase reserve/partial coverage, provider failure/timeout, zero/reject/abstain, duplicate/campaign guards, Unicode, audio track 1, VFR/nonzero timestamps, optional audio/frame interfaces, technical sidecars, interrupted export and cache corruption. Existing tests were retained without weakened assertions.

`python -m chomp.evaluation.smoke --out outputs/cycle2-smoke-verified`: **15/15 technical cases passed**, including corruption rejection. The fourteen valid-media registrations pass dataset validation with development and synthetic holdout groups. JSON Schema validation passed for the annotation template and fourteen generated source-event files. `uv pip check --python .venv/Scripts/python.exe`, compileall and git diff whitespace checks passed. Exact dependency/environment versions are in baseline-test-report.md. No ASR/diarization inference framework was needed by the automated tests.

The test-count increase is coverage, not a timing-speedup claim. Runs differ in filesystem/cache/host activity, and their elapsed times do not establish a performance improvement. Local tests establish Windows 3.13.14 behavior; remote matrix outcomes are tracked separately.

## 6. Benchmark comparison

Observed first synthetic case, same generated source/events/rights/protocol:

| Experiment | Proposals | Selected | Synthetic proposal recall | Sent fake-provider calls | Recorded analysis seconds |
|---|---:|---:|---:|---:|---:|
| A heuristic production | 2 | 1 | 1/1 | 0 | 0.692 |
| B semantic production, fake judge | 2 | 1 | 1/1 | 2 | 0.668 |
| C Director First, fake provider | 2 | 1 | 1/1 | 2 | 0.164 |
| D accepted C report → renderer | 2 | 1 | 1/1 | same two analysis calls from C | same analysis, export separate |

Budgets differ and are explicitly reported as unmatched. D reuses C's editorial analysis. These values test adapter/integration mechanics, not semantic superiority or matched efficiency. Human precision, preference, real-world recall and unsupported visual/audio judgments remain null. `synthetic-results.json` and synthetic-benchmark-example.json/.md contain actual observed technical outputs, with no private media paths. Metric definitions/raw denominators and unavailable measurements are documented in benchmark-metrics.md.

## 7. Real-footage verification

Reused an existing local private-evaluation authorization and frozen source-clock Whisper transcript for one source section, 2,400,000–2,520,000 ms. Historical Director responses came from the prior successful gpt-5.6-sol/Codex CLI evaluation; this cycle replayed four validated responses with **zero new provider calls**. A failed historical unsupported-model report was initially rejected by cache-only replay; the replay utility now rejects unsuccessful historical reports before work begins. No unsupported-model inference was retried.

New local exports use neutral IDs V01/V02, with a private mapping:

| Workflow | Source interval(s), ms | Decoded audio level | Source/output waveform correlation |
|---|---|---|---|
| Heuristic V01 | 2,466,050–2,495,090 | −20.995 dBFS | 0.999795 |
| Heuristic V01 | 2,446,170–2,468,350 | −27.833 dBFS | 0.999895 |
| Cached Director V02 | 2,403,250–2,445,370 | −24.490 dBFS | 0.999752 |

All are H.264/AAC 540×960, passed full decode, and measured zero envelope lag at 20ms resolution with zero stream-start delta. This checks selected-track preservation and source-clock audio agreement; it does not constitute human listening/lip-sync approval. Six source/output comparison images were generated; two were inspected by this agent. Sampled frames retain the source's streamer and gameplay, while showing the full-frame composition limitation. Caption files, exact source evidence, edit specs, decisions and diagnostics remain local under `outputs/cycle2-private-speed-v2/`. No footage/transcript/images were added to Git.

Real quality annotations are absent. No human preference or approval is invented. Moving-webcam, multi-speaker, unseen-creator, other-category and other requested real scenarios were not comprehensively verified in this cycle.

## 8. Remaining defects, research and security

The transparent-webcam/full-frame fallback remains visibly conservative. Automatic streamer identity and essential-gameplay retention are not solved by synthetic colored boxes. ASR transcript fragments/word timing, caption pixel overflow and independent readability still need playback/annotation. Full long-VOD semantic quality, actual memory peaks and real API dollar cost remain unmeasured. The complete authorized 24-VOD dataset and independent creator-disjoint human holdout are not supplied.

DEEPSCOUT should supply discovery policy, semantic audio/visual models, adaptive retrieval/fusion, non-dialogue acceptance and experimental evidence. Extension locations, compatibility/migration limits and coordination-sensitive files are in research-integration-contract.md and coordination-ledger.md. No external research coordination or approval is claimed.

Rights checks and manual review remain active. Publishing stays disabled. Transcripts/chat/model text remain untrusted data; schemas, source-relative integer intervals and geometry checks bound downstream effects. Existing Codex provider restrictions remain active. Media, private transcripts, caches and auth files were not committed or uploaded. New media/outputs directories are ignored. Source/evidence/artifact hashes prevent accidental source/edited-output substitution. No paid API usage or heavy model download occurred.

## 9. Performance and reproduction

VFR normalization can duplicate frames and change encode work; only the affected legacy path opts in. Full source hashing, probing and decoded waveform checks add I/O; multi-clip hashing is conservative and not yet optimized. Caches reuse validated analysis and bounded frames/audio observations. No end-to-end performance or subjective quality improvement is claimed from these small runs.

From this worktree on Windows:

```powershell
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe -e '.[dev,director,facecam]'
.venv/Scripts/python.exe -m pytest -q -ra
.venv/Scripts/python.exe -m chomp.evaluation.smoke --out outputs/new-cycle2-smoke
.venv/Scripts/python.exe -m chomp.evaluation dataset-validate outputs/new-cycle2-smoke/dataset.json
.venv/Scripts/python.exe -m chomp render-director --report <director.json> --output outputs/review --acknowledge-permission
```

On Linux activate `.venv/bin/activate` and use `python`; FFmpeg must provide libass subtitles. Optional local real replay: `python tools/cycle2_local_review.py --prior-report <successful-private-report> --analysis-cache <existing-cache> --transcript <frozen-source-clock-json> --out <new-ignored-output> --rights-ref <existing-evaluation-reference>`. Any cache miss fails without invoking a provider.

Git sequence: audit `3e3f122`; VFR fix `ef53918`; benchmark/adapter `c52b873`; Director export/budgets `6571def`; research interfaces `5ed7820`; tests/replay `3930ead`; final code/CI checkpoint `734aa37`. Local BASE-10 milestone is tagged `cycle2-base10` at the final code checkpoint. Final documentation/delivery commits follow it; resolve the delivered head with `git rev-parse work/cycle2-keystone` and see delivery-status.md. Only the designated working branch is pushed; no force push, branch deletion or main merge is permitted.

Next cycle: independently review the local neutral packets first; register authorized real sources/splits before annotation; resolve transparent-composite composition with comparative evidence; integrate DEEPSCOUT providers behind these contracts; run matched-budget experiments and collect actual human precision/recall/completeness before making quality claims.
