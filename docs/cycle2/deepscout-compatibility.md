# DEEPSCOUT delivery reconciliation

During delivery, draft [PR #12](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/12) became available. Inspected head: `66430342237bb2c96118e9cbb14ff865b713ebcd`, branch `research/ds01-deepscout`, common base `24bf2f5f4ad2172e4b18728b198fe7d3e9d65aff`. Its 15 added files contain RADAR-X, tests and research records without changing production modules/dependencies. Its report limits evidence to targeted/synthetic evaluation and recommends an isolated shadow experiment, not production adoption.

A real naming collision was found: KEYSTONE added `chomp/research.py`, while DS-01 adds `chomp/research/__init__.py` and `radar_x.py`. The package would shadow the module. KEYSTONE interfaces now live in **`chomp/research_interfaces.py`**. No DS-01 files were changed.

Compatibility check: an isolated detached worktree at KEYSTONE `aef6d0c` received a no-commit merge of the exact research head. No merge conflicts occurred. The complete combined suite passed: **370 tests in 53.41 seconds**, including all 15 DS-01 tests. This was a temporary working-tree merge; research code and a merge commit were not added to KEYSTONE, the remote PR or main. The temporary merge was aborted after verification.

DS-01 `chomp-radar-x/1` remains discovery-only. A future opt-in benchmark adapter should map `context_ms` to native proposal.start_ms/end_ms and retain core, anchor, evidence IDs, lane ranks/strengths, priority, missing_lanes, verification_status and rationale. Preserve explicit availability. Unverified/exploration proposals remain unselected until editorial/evidence validation. Exploration without evidence is not an accepted clip. Validate source identity and integer half-open spans. Rank-fusion priorities are not semantic probabilities or human scores.

RADAR-X Evidence uses a producer/strength/source contract and <=60-second evidence bound. KEYSTONE's broader envelope needs explicit conversion with provenance, rather than relabeling utterances as visual observations. Existing production Observation and Director merge_signals cannot directly consume RADAR-X dictionaries. No implicit conversion or research ranking default was enabled.

The authoritative production baseline remains Director First. Next action is a separately configured shadow comparison with the same authorized source annotations, budgets and ChompBench protocol, then independent real-VOD review. Shared-schema coordination remains open. Passing combined tests does not approve the research algorithm or establish entertainment quality.
