# BASE-10-R1 — independent review corrections

Review baseline: `c467fe2772ad3463574da0a2cb082cb931fc0823`, branch `work/cycle2-keystone`, draft PR #13. The coordinating reviewer independently verified the earlier Windows/Ubuntu Python 3.11/3.13 CI. This record describes subsequent corrections, not a retroactive claim that the original issues were absent.

## REV-01 — contextual-investigation budget

**Root cause:** the default subtracted only one final response from the shared total, allowing 119 discovery calls to consume capacity nominally configured for four context investigations.

**Decision/implementation:** `BudgetPolicy.for_total(120)` now returns **115 discovery / four investigation / one final**. Validation requires all phase allocations to fit the total. CLI and Python share this constructor. Tiny totals retain final capacity and scale discovery/context explicitly; a one-call total cannot silently claim complete discovery. Discovery ordering, candidate priorities and semantic architecture remain unchanged. Cache hits do not consume newly sent provider-call capacity.

**Verification:** a deterministic two-hour speech-heavy fixture has 120 utterance-bearing windows. Its first run sends 115 scans, four separate bounded context investigations and a final response within 120 calls; coverage remains explicitly partial. Resume scans the remaining five windows, reuses the five validated decision responses and reaches complete dialogue coverage. A subsequent replay sends zero calls. The default-allocation test covers totals 1–300; invalid 119/4/1 allocation is rejected. The existing partial-resume test still checks actual partial coverage and cache invalidation, updated for the new reserved-capacity behavior.

**Limit:** reserving capacity does not prove that retrieved context is semantically sufficient. Provider failures/context size limits still fail explicitly. Tests use fakes; no paid inference was needed.

## REV-02 — temporal grounding is not contextual completeness

**Root cause:** valid source-relative references and preserved source spans could be read as proof that the selected opinion, joke or story is self-contained.

**Decision:** this remains a **KR-01 through KR-08 research dependency**. No new semantic architecture, context-ranking model or training was introduced. Reports explicitly label temporal coverage and leave human context judgments unavailable without independent ratings. Human review must inspect the source lead-in/follow-through, implied referents, setup, intent and payoff, not merely matching timestamps. The old semantic selection's claimed completeness is not independently certified by rendering it.

**Verification:** tests assert that perfect annotated-event span coverage does not populate human contextual-completeness or precision values. The new private page provides source-context playback and blank independent review fields.

**Limit:** pending research and human review; no entertainment-quality improvement is claimed.

## REV-03 — event, clip and human coverage

**Root cause:** event-level “any successful selected clip” coverage used a name that could suggest every selected clip was complete; unmatched selections and missing human reviews were easy to overlook.

**Implementation:** explicit `event_essential_span_coverage`/`event_acceptable_boundary_rate`, `matched_clip_essential_span_coverage`/`matched_clip_acceptable_boundary_rate`, per-selection event matches, raw matched/unmatched counts, unmatched-selection rate and human-reviewed coverage. Independent reviewed, determinate and uncertain counts remain separate. Unknown context ratings/precision stay null. On incomplete source annotations, the unmatched rate is null because unannotated events may exist; raw unmatched-to-known-event counts remain available. Old completeness/boundary fields are deprecated **event-level temporal** aliases.

**Verification:** one annotated event with a complete clip, an incomplete duplicate and an unmatched selection produces event coverage **1/1**, matched-clip coverage **1/2**, and unmatched selections **1/3**. Missing human labels remain null; a supplied empty review template has known coverage zero and unknown precision/context ratings. JSON and Markdown expose the separate denominators.

**Limit:** span preservation is an annotation-based temporal proxy; unmatched does not independently mean uninteresting or contextually incomplete. Reviews are not pooled into fabricated consensus.

## REV-04 — audio alignment naming/schema correction

**Root cause:** `av_sync_error_ms` was populated from audio-envelope agreement with the source. Neither audio correlation nor container start-time agreement measures actual audiovisual/lip synchronization.

**Implementation/migration:** reports retain the `chomp-event-benchmark/1` envelope and add `metric_definitions_version: chompbench-metrics/2`. Audio-only results now populate `audio_envelope_absolute_lag_ms`, explicitly `is_audiovisual_sync: false`. **`av_sync_error_ms` is null** until real A/V measurement exists. `inspect_export` uses `measurement_schema: chomp-export-technical/2`, `audio_envelope_lag_ms`, `audio_envelope_correlation` and `container_stream_start_delta_ms`; old unambiguous diagnostic keys remain deprecated aliases. The reader accepts legacy/new envelope keys and rejects conflicts. Historic numeric A/V-labeled reports must not be interpreted or pooled as actual A/V measurements. The committed synthetic example was regenerated with v2 definitions.

**Verification:** both old and new sidecars containing −20 ms audio lag produce 20 ms absolute audio alignment and null actual A/V sync. Real exports retain decoding/audio checks; no lip-sync approval is inferred.

**Limit:** envelope search has 20 ms resolution and bounded lag range; unavailable/insufficient variation yields null. Human listening and audiovisual review remain pending.

## REV-05 — experimental lane-order truncation

**Root cause:** iteration order silently allocated the candidate cap, allowing early lanes to exclude a later populated lane.

**Implementation:** collection without overflow is unchanged. Overflow now raises unless `experimental_lane_order=True` is explicit. The result identifies the experiment, keeps `production_fusion_enabled: false`, and reports input/retained/omitted counts by lane, completely excluded lanes, empty-input lanes and actual lane order. No production call site or fusion default was introduced.

**Verification:** tests show a populated dialogue lane completely excluded by two earlier energy proposals, demonstrate that reversing lane order changes allocation, distinguish an empty visual lane, and report every populated lane excluded under an explicitly experimental zero budget.

**Coordination:** the project owner's requested handoff was [posted to DeepScout PR #12](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/12#issuecomment-7166657985). It requests a configurable, provenance-preserving allocation contract, exclusion diagnostics, matched-budget shadow comparisons and independent labels. The policy decision remains with the research workstream; no reply or approval is claimed.

## REV-06 — reconcile cached exports against verified inputs

**Root cause:** reuse iterated only the manifest's present rows and verified their supplied hashes. A removed row/caption or modified roster could evade that check.

**Implementation:** bridge cache fingerprint advances to **v2** and includes the renderer revision. Before reuse, compare manifest source/run/rights/config/counts and review-page/manifest pointers with verified controller inputs; require the exact selected candidate roster once each; check source identity, track, exact clip/candidate intervals and anchor; compare editorial decisions with the accepted report; reconcile canonical manual manifests and edit specs, including titles/framing/audio metadata; require expected artifact names/paths within the render job and actual hashes; regenerate required caption text/count from the hash-matched transcript and verify the caption artifact and edit hash. The native candidate ledger must also match the validated adapter output. Fresh exports pass the same reconciliation before returning. Weak v1 cache fingerprints are not reused.

**Verification:** 17 mutation cases cover missing rows, forged counts, duplicated entries, unselected IDs, changed manifest/clip source identity, changed bounds, removed preview/captions/hashes/cue counts, wrong track, altered campaign, changed review pointers and a forged cached title. Existing interrupted-export recovery, artifact modification, source/evidence mismatch and zero/reject/abstain tests remain active. All freshly rendered review exports pass the stronger verification.

**Limit:** these checks detect stale/inconsistent local artifacts and metadata; they do not constitute independent source-content/semantic proof or human approval.

## Fresh real-video review delivery

The user's follow-up explicitly requested **fresh renders plus newly discovered moments**. Private local page: **http://127.0.0.1:8765/**. Files remain under ignored `outputs/base10-r1/review-room/`. The page was opened in Codex and checked for playable source/export media, paired playback, source context, transcript escaping and blank review fields.

- N01: source **00:43:46.930–00:44:07.060**.
- N02: source **00:46:27.905–00:46:42.905**.
- N03: source **00:43:14.750–00:43:50.710**.
- E01–E03: the earlier three intervals, newly re-rendered with their existing editorial decisions explicitly identified.

New moments come from a fresh local Whisper-small CPU transcription of source 42:00–47:00: **42 segments in 31.06 s**, using already installed dependencies/weights. Discovery ran against a fresh output root and recorded `audio_cache_hit: false`. No old provider responses or analysis cache were copied into that new discovery. All three new intervals differ from the earlier selections. Rendering used a new packet directory; **zero cached MP4s were reused**. Deterministic encodes of an unchanged earlier interval may have the same content hash; generation logs/timestamps and distinct fresh output directories establish that they were re-encoded rather than copied.

All six portrait exports passed full decode and audible selected-track checks. New-export waveform correlations were **0.999875, 0.999771 and 0.999741**; corresponding output levels were **−25.87, −20.93 and −36.18 dBFS**. These are audio-to-source checks, not actual A/V or semantic-quality scores. Source-context videos include ten seconds before/after each interval where available; source/full portrait comparison PNGs are included. A local read-only byte-range server serves only the packet root, with cache disabled and no directory listing. UI feedback downloads native ClipLedger files; no ratings or approvals are prefilled or sent remotely.

Restart if needed, from the worktree:

```powershell
.venv/Scripts/python.exe tools/serve_private_review.py --root outputs/base10-r1/review-room --port 8765
```

The page's paired controls were checked after loading both players; a UI clock-offset check was within roughly 50 ms during that sampled playback. This is a player-control check, not a measured source audiovisual synchronization result. The existing conservative transparent-webcam composition remains visible. Human context, caption readability, crop quality, duplicate-content judgments and entertainment quality are **pending**. No footage/transcript/review image was pushed to GitHub.

## Actual validation and handoff

- Complete local regression command: `.venv/Scripts/python.exe -m pytest -q -ra` — **382 passed in 68.70 s**, zero failed/skipped. This includes 27 additional R1 review/UI/server cases beyond the independently reviewed 355-test revision. The earlier R1 pass before three extra pointer/title mutations was 379 tests in 62.28 s; no performance conclusion is drawn from those runtimes.
- Complete matrix command: `.venv/Scripts/python.exe -m chomp.evaluation.smoke --out outputs/base10-r1/verified-smoke` — **15/15 passed** after the renderer-revision/pointer binding. The initial complete R1 matrix at `outputs/base10-r1/smoke-matrix` also passed 15/15. Generated source registration validation passed.
- `compileall`, `uv pip check` and whitespace validation passed. No paid model calls, new models or training were used. Fresh real-ASR inference was local using existing weights.
- Coherent implementation commits: `b403299` budget correction; `ff0850a` cached-export reconciliation; `39952ce` metric/audio naming; `3a477ce` review regression/lane diagnostics; `9147eca` fresh review tooling; `b9d5126` pointer/metadata/renderer-revision binding. Resolve the delivered head with `git rev-parse work/cycle2-keystone`; the final submitted SHA and remote CI are reported on PR #13 and in the delivery reply.
- Keep PR #13 draft. No production-baseline/main merge. KR-01–KR-08 semantic/context investigations, independent human judgments and broad real-VOD/holdout validation remain outstanding.

Final local code checkpoint: `b9d5126`. The six already-fresh private exports were additionally reconciled against their historical generation-controller receipts using the current source/caption/manifest verifier, without copying or reusing a rendered MP4. All three new and all three earlier-rendered files passed. Renderer revision remains 8; the extra final change binds that revision and the review pointers into subsequent cache verification.

Remote R1 checkpoint `c04157d129af2a3585a7efef9ddb32d1c5fb29fa`: [CI run 45644567223](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/actions/runs/45644567223) passed all four Windows/Ubuntu Python 3.11/3.13 jobs, including the complete smoke matrix. The final submitted revision adds three pointer/title mutation cases and renderer-revision binding; its exact final head/CI is reported on PR #13 and in the delivery reply rather than misattributed to the earlier run.
