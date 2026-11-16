# EDIT-FX-03-F — Verification checklist

The initial implementation session left this checklist for the next model. Sol used it during the continuation. The actual tests, local artifacts, measurements and remaining human review are in [EDIT-FX-03](EDIT-FX-03.md). The checklist below preserves the acceptance targets for future runs.

## Start here

- Workspace: `D:\AI _Clipper\chomp-chomp\edit-fx-03`.
- Branch: `feat/edit-fx03-shotcomposer`.
- Base: `work/edit-fx-02-subjectlock`, `8dd1cb52634c4eb7dd639499a4eda5b4a4709d77`.
- Read `docs/tasks/EDIT-FX-03.md`, `docs/editing/shotcomposer.md`, `docs/editing/shotcomposer-research.md` and `docs/agent-task-registry.md`.
- Inspect current `git status`, HEAD and PR. Preserve unrelated user changes. Do not merge or claim accepted.
- Runtime is existing Python/FFmpeg plus optional `jsonschema`; OpenCV needed for inherited vision tests. No new model/provider invocation is needed for compiler validation.
- Use a cheaper coding model for this assignment. The implementation session did not launch or assign work to another model. Record your actual model/provider provenance.

## Priority 1: code and contract checks before real renders

Create focused `tests/test_shotcomposer_contract.py`, `tests/test_shotcomposer_compiler.py` and `tests/test_shotcomposer_workflow.py`. Read the code and fix defects rather than writing assertions that merely repeat implementation strings.

1. Run syntax/import checks, schema self-validation and packaging/install checks. Verify schemas ship in installed packages through the existing package-data glob. Exercise both `chomp shots` and `chomp director shots` entry points.
2. Assert strict rejection for unknown fields/operators, duplicate JSON keys/IDs, NaN/Infinity, booleans/fractional times/geometry, invalid rational rates, stale source/evidence hashes, bad source audio selection and changed files. Exercise negative/gap/overlap/out-of-range/subframe shots and max-resource boundaries.
3. Source evidence: sparse frames never certify a whole shot; inferred/hypothesis/unverified intent remains review-required; people need identity-consistent interval evidence. Invalid old Director decisions reject/abstain must not render. A forged/mismatched binding or missing subject must fail. Correct full-source context must not claim any subject identity.
4. FX02: attach current tracker revision without inventing continuity. Preserve all visibility states. A lost tracker does not mean confirmed absence. Source overlay covering a protected subject blocks. Explicit human review may resolve a detector gap, with warning. Cut indices reset interval geometry. Validate malformed/huge/missing track observations and stale source IDs.
5. Geometry: independent mathematical expected crop and projection for contain/cover and SAR. Required subjects/HUD must survive both path endpoints and intermediate extrema. Test the swept-region case where safe endpoints pass across an inset/caption in the middle. Test PiP z-order, distinct two-person identity, multiple scene layouts, excessive scaling, image boundaries, and rounding by actual output pixels.
6. Motion: zoom-in/out/return with linear/smoothstep, key order, finite bounds, speed rejection, crop movement from a saved FX01 path, loss holds, no path smoothing silently changing approved geometry. Verify graphic rejection on moving slots.
7. Graphics: known color/position/timing for outline/spotlight, source-linked target, inset/caption conflicts, no graphic past shot end, graphic clipping. Validate quotes/FFmpeg-looking text in `reason` stays plain metadata. No raw operator expressions or asset paths may execute.
8. Transitions: required outgoing region coverage extends through incoming dissolve. Reject first-shot dissolve, out-of-range duration, unquantizable low-rate duration and source cuts. An unsupported effect never silently becomes a successful no-op. Optional future requests remain reported as not rendered; required future requests block.
9. Workflow: immutable plan/evidence/SRT snapshots, parent revision hash, existing-dir refusal, partial failure report, source unchanged before/after, original asset integrity. Existing v0.5 renderer/rerender remains untouched by absence of `shot_plan`. Legacy migration is an envelope, not a falsely equivalent new graph.

Do not authorize network downloads in tests. Avoid timing assertions dependent on CPU speed. Use generated tiny media; do not commit private clips.

## Priority 2: actual FFmpeg temporal and spatial proof

Create a deterministic source with visible per-frame IDs, a moving target, protected edge HUD, independently placed changing camera panels, and audio pulses tied to source timestamps. Produce known expected colors/regions so incorrect view selection is observable in decoded pixels, independent of compiler code.

Test 24000/1001, 25, 30000/1001, 30 and 60 fps, plus VFR, rotated portrait, anamorphic SAR, odd source dimensions, 8/10-bit decode and yuv420p/NV12 when supported. Include delayed audio, multiple audio tracks, nonzero seek, a final non-frame-aligned end, and cut/dissolve boundaries not initially on the output grid.

- Inspect `fps`, `trim`, `settb`, `split`, overlay timeline enable, blend frame index, dynamic scale reconfiguration, crop expressions and framesync on the installed FFmpeg build. Documentation-informed design is not runtime proof.
- Cut target: actual view switch within one output frame of request; declared frame mapping must match measured output. Dissolve must mix two views of the same progressing source frame, without overlapping-clip time shortening.
- Duration target: within one output frame, with AAC/container tolerance explicitly separated. Quantify real audio pulse offset, selected-track identity and sample alignment. No visual transition may introduce a new audio discontinuity. Original audio is re-encoded by the inherited renderer, not byte-copy output.
- Check first/last frames, no unintended black/blank frames, no extra view flashes, no frozen source during zoom, no escaped subtitle/graphics input, no interval skipped or replayed by editing.
- Inspect the runtime decode and media checks from `media.render_preview` alongside the synthetic frame and pulse results.

Fix defects in focused commits. After these pass, run the inherited full suite in a clean Windows environment and the repository's Linux/Windows Python 3.11/3.13 CI matrix. Capture optional-test skips. The prior local environment workaround is documented in FX02; prefer a fresh explicit `[dev,director,facecam]` environment if practical. Do not blindly use an old environment just to claim all tests passed.

Setup and test commands for a clean verification environment:

```powershell
Set-Location -LiteralPath 'D:\AI _Clipper\chomp-chomp\edit-fx-03'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,director,facecam]'
.\.venv\Scripts\python.exe -m pytest tests/test_shotcomposer.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

## Priority 3: JAY3-03 — still open

Source: `D:\AI _Clipper\chomp-chomp\AI_Clipper-Chomp-Chomp-\test.mp4`.

Expected SHA-256: `f936b46d4510e8774dab57db20e3cdca95b85a57245ef32d6d636503a5d8cfe0`.

Exact required interval: **[73350, 90170) ms**. Match the existing audio stream, profile, captions and source interval. Earlier FX02 recorded 3,700ms source-overlay coverage and 12,103ms insufficient tracking evidence. These are state-accounting totals, not proof that gameplay is absent everywhere in those ranges. The prior method still leaves a low-information game strip during source enlargement.

Existing local evidence:

- `..\edit-fx-02\outputs\edit-fx-02\review.html` and `compare\jay3\review.html`.
- `..\edit-fx-02\outputs\edit-fx-02\compare\jay3\metrics.json`, `timeline.png`, `sample-001.jpg` through `sample-021.jpg`.
- `..\edit-fx-02\outputs\edit-fx-02\annotations\jay3-seed.json` and `jay3-labels.json`.
- Prior v0.5 input: `..\edit-fx-01\outputs\edit-fx-01\jay3-new-run\4bb25b107c5c451aeee784d0\candidate_03.edit.json`.

This session viewed only existing `sample-006.jpg` at **76,100ms**, which supports the narrow-strip diagnosis at that instant. It did not watch the sequence, approve any reaction span, or determine source-verified return timing. **Do not use 4s/6s from the synthetic example, or 77500/81500 from the uploaded report's illustrative JSON, as real Jay3 switch times.**

Watch the source and old output first. Establish the actual camera container/visible person and semantic value of a reaction cut. Record exact supported start/return frames and review references. Build a native plan using those independently reviewed regions and attach the original FX02 evidence. If a full-streamer shot cannot be justified or its person is absent, retain a blocked result and show refusal, not invented success.

Then render exact-source A/B baseline/ShotComposer outputs. Save source/output contact sheets around every cut and dissolve, frame-grid timing report, audio clock comparison, target/HUD readability/coverage, crop travel, actual source gaps, and human full-playback ratings. Obtain owner approval before claiming JAY3-03 fixed.

## Priority 4: independence, performance and editorial review

- Preserve the old unseeded Emiru output as regression. The same source is `..\AI_Clipper-Chomp-Chomp-\test2(emiru vod).mp4`, SHA-256 `b501192d8863d3534c281f07ca939c0ff24044c599f52f1be84e95e864c1a209`. Verify the actual file name/hash before use. Use a bounded selected interval, not a new full-VOD run unless needed.
- Add a different creator/source: multi-person commentary/evidence panel plus IRL or no webcam. Freeze one untouched holdout before tuning. A synthetic test does not prove creator generalization.
- Source-matched human comparison rates story clarity, cause/payoff preservation, reaction relevance, readability, transition appropriateness, distracting movement and overall watchability. Prefer at least three independent reviewers where possible. Stable cut-only may win.
- Benchmark same machine/source/settings at 540×960 and 1080×1920: elapsed time, CPU, peak Python **and FFmpeg** memory, graph length, decode count, output size and errors. Separate cold/warm runs. A 25% static-layout overhead target is provisional. The all-clock multi-view graph may exceed it; do not claim efficient until measured.
- Optimize only after measurement. Candidate low-risk work: share identical view branches, prune inactive time windows without shifting PTS, avoid redundant source hashing across a single locked controller transaction. Never reuse artifact approval when the plan or renderer changes.

## Completion report and integration boundaries

Record exact HEAD/environment, commands/results/skips, media hashes, failed conditions/fixes, measured timings, human reviewer decisions and unfinished items. Update FX03 task manifest and regression index without rewriting previous results. Keep the PR draft until the agreed acceptance gate is satisfied. **Do not merge automatically.**

Remaining separate implementation decisions: richer observation-interval certification, automatic orchestration from selected Director candidates, animation-aware target graphics, licensed asset registry, native equivalent migration of old blur layouts, persistent renderer-versioned cache, and FX06 artifact-bound approval integration. Circle/arrow, wipe/slide, temporal edits, kinetic captions and sound remain correctly unavailable/delegated. They are not hidden requirements for declaring the currently implemented subset tested.
