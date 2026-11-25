# Metrics and denominators

All rates include raw numerators and denominators in `metrics`. Unavailable/zero-denominator values are null. Zero selected clips never imply perfect precision. The original scalar fields remain for compatibility.

| Measurement | Definition / availability |
|---|---|
| Proposal recall | Annotated events with at least 80% core coverage by any proposal / annotated events. Null unless source annotations are complete and nonempty. Discovery quality is separate from selection quality. |
| Proposal density | Proposal count × 3,600,000 / source duration_ms. |
| Proposed footage | Existing `proposal_minutes_per_source_hour` uses union coverage; raw sum and unique union are separately exposed as `proposed_footage_ms` and `unique_proposed_footage_ms`. Overlapping proposals are not merged into invented events. |
| Human-approved / selected precision | Existing ClipLedger precision@min(3,N), with its raw good/selected counts and completeness gate. Null without independent clip reviews. Multiple reviewers remain separate assessments. |
| Setup/payoff completeness | Selected annotated events whose essential span is fully preserved / selected annotated events. This is interval coverage, not semantic model certainty. |
| Acceptable boundaries | Selected annotated events with a selected interval satisfying both admissible edge ranges / selected annotated events. |
| Duplicate selections | Distinct extra selected indices matching the same annotated core / selected count. Null without sufficient complete annotated event identity. An extra clip counted through two annotations is counted once. |
| Correct empty result | Empty selection on a completely annotated, full-source, successfully analyzed negative source / eligible negative sources (one per run). Null for failures, partial discovery and section-only runs. |
| Caption overflow | Boolean count / measured clips from independently supplied technical measurements. Null when pixel overflow was not actually measured. Caption text wrapping tests do not establish pixel overflow or readability. |
| Missing/silent audio | Boolean count / decoded measured clips. `technical.inspect_export` verifies actual audio decoding and also distinguishes expected source silence from unexpected silence. A stream's existence alone is insufficient. |
| Synchronization | Mean absolute reliable envelope lag against selected source interval, with measured count and 20ms resolution. Constant tones/insufficient variation yield null. Raw stream-start delta and sample-aligned waveform correlation are recorded separately. This does not establish human lip-sync approval. |
| Streamer retention / protected-region visibility | Null absent independently verified identity/region labels. Existing `subject_compare` and `layout_compare` remain available; no new semantic identity score is synthesized. |
| Camera stability | Existing layout evaluator exposes travel, keyframe velocity/acceleration, reversals and layout changes. ChompBench aggregate remains null when no compatible measurements were supplied; no uncalibrated quality score is invented. |
| Runtime / peak memory | Actual recorded analysis seconds; peak memory is null because no cross-platform sampler was executed. Render time is separate in review manifests and is not misrepresented as analysis time. |
| Provider calls / cache | Recorded sent attempts and validated hits; per-phase counts distinguish scan/decision. Hit rate = hits / recorded call attempts including cache lookups. Historical cache replay has zero new provider calls. |
| Actual API cost | Recorded cost only; subscription dollar cost is unknown/null. Heuristic execution records zero remote API spend. No measured-token estimate is substituted for actual cost. |

The optional `chompbench-technical/1` sidecar binds source_id, job_id, duration_ms and each candidate ID/source interval to decoded measurements. Wrong runs, intervals, duplicate IDs and invalid values are rejected. Partial technical coverage retains its actual measured denominator. Technical check failures should be reported, not removed from the measured population.

Complete source annotations may be evaluated against partial discovery, which can honestly expose misses; the report preserves coverage/status. No partial analysis can receive a successful full-source empty-result score. Synthetic labels are always marked synthetic, even when mathematically complete for the generated fixture.
