# Stage 1 / Section 6 — Intelligent clip boundary refinement

Base audited: `feat/vod-foundation` at `37d8ff2d185fa4410ea5dd2097e2e1684923b23d`.

## Audit findings

The original `chomp/boundaries.py` gathered **every** transcript segment intersecting the candidate extent plus `context_ms`, expanded to the first/last segment, and trimmed long intervals at a fixed 45%-before-anchor offset. In a continuous discussion this routinely produced the configured 60-second maximum, potentially cutting a word or sentence at an arbitrary millisecond. It also widened quiet clips to include unrelated speech, did not distinguish a lack of evidence from utterance-aligned timing, and had no way to consume the existing lightweight audio curve. It never claimed semantic inference (correctly). The input models use **source-relative integer milliseconds**; the candidate anchor must remain inside the refined interval. `refine_boundaries` mutates the `Candidate`, returns `BoundaryDecision(start_ms, end_ms, method, note)`, and the pipeline records this decision.

## Algorithm

1. Validate candidate, configuration, and transcript timestamps. Keep the event anchor unchanged. Search a bounded window around the anchor (not around all of the potentially broad discovery candidate).
2. Select the closest non-empty transcript segment when its interval is within **3 seconds** of the anchor. This seed can belong to *any speaker*; speaker labels do not affect timing. Sort relevant segments locally before expanding.
3. Expand backward and forward over adjacent segments across gaps of at most **1.5 seconds**, within typical budgets of **8 seconds before** and **10 seconds after** the anchor (adjusted downward by `context_ms`). A single longer gap of up to **6 seconds per side** can be bridged when its nearest utterance is within 5 seconds of the anchor and fits hard context budgets of **12 seconds before** and **16 seconds after**. This handles delayed reactions, but is only a *proximity heuristic*, not real setup/payoff recognition.
4. Pad segment-aligned bounds by **350 ms before** and **650 ms after**, clipped to the source clock. Retain the original anchor. Apply a **34-second soft duration cap**, or the caller's smaller `max_length_ms`. If an ASR segment itself exceeds the budget, make a deterministic anchor-preserving hard cut and report `transcript_assisted_hard_cut`.
5. Without anchor-near speech, keep a bounded portion of the existing discovery window centered near the unchanged anchor: at most **30 seconds** or the caller's lower maximum, enforcing `min_length_ms` where the source permits. Optional already-computed `audio_levels` (dBFS per bin, default 1 second) may snap provisional fallback edges to **-50 dBFS or quieter** bins within 1.5 seconds. They never decide the payoff by looking for spikes and are not required. Label methods `signal_padding_fallback` or `signal_padding_audio_silence`.
6. Respect source boundaries, max/min clip lengths, and update `candidate.transcript_excerpt` in chronological order. All methods explicitly require human review. Repeated refinement on the same inputs is stable for the covered fixtures.

`context_ms` controls the transcript proximity budgets, not a request to fully include all nearby transcript. Extremely low values still permit small segment-aligned padding; semantic identification remains out of scope. The implementation uses only Python's standard library and existing `models` and `signals` helpers.

## Integration

The **existing** `chomp/pipeline.py` call requires **no changes**:

```python
boundary_decisions[id(c)] = asdict(refine_boundaries(c, transcript, duration_ms))
```

The decision dataclass and its fields are unchanged; new `method` values are deliberately descriptive and still begin with `transcript_assisted` where applicable. The candidate is still mutated before ranking and captions.

**Optional follow-up integration request (not made in this PR):** `pipeline.process` already obtains a dBFS `audio` list from `media.scan_audio(media)` before boundary refinement. The pipeline owner may choose to call:

```python
refine_boundaries(c, transcript, duration_ms, audio_levels=audio, audio_hop_ms=1000)
```

This is a backward-compatible keyword-only input. It does not decode any additional audio or require speaker diarization. It is intentionally not wired into `pipeline.py` here due to file ownership. No shared model changes are requested.

## Test evidence (synthetic, NOT human clip-quality scores)

Run `python -m pytest -q tests/test_boundaries_stage1.py` from repo root. Fixtures include short reaction, long conversation, setup/payoff with long pause, other-speaker speech, overlapping and incomplete ASR, quiet moment, source opening/ending, missing audio/transcript, validation, minimum/maximum duration, audio silence-bin snapping, deterministic repeatability, and 200 seeded randomized timestamp cases.

On Python 3.13.5, the new boundary-specific test module passed **22 tests** locally. The broader repo-wide integration suite and Windows/Python 3.11 matrix require GitHub Actions on the PR; local evaluation here did not execute that full matrix.

Baseline comparison executed on the original algorithm copied *verbatim* from the base branch, against the same synthetic fixtures:

Reproduce the exact synthetic before/after fixture spans and a fresh timing microbenchmark with `python tests/bench_boundaries_stage1.py` from an editable install. The script retains a copy of the original 37d8ff2 timing implementation as its explicit baseline. The numbers below were obtained in the initial local run; measured runtimes vary by host.


| Fixture | Before [start, end] ms / duration | After [start, end] ms / duration |
| --- | --- | --- |
| Short reaction | [34500, 63000] / 28.50s | [45150, 53150] / 8.00s |
| Continuous conversation | [24000, 84000] / 60.00s | [44650, 60350] / 15.70s |
| Setup + delayed payoff | [12500, 37000] / 24.50s | [12650, 36650] / 24.00s |
| Multi-speaker dialogue | [12000, 32000] / 20.00s | [15650, 26650] / 11.00s |
| Quiet anchor | [35000, 55000] / 20.00s | [35000, 45000] / 10.00s |
| Missing transcript, broad candidate | [20000, 80000] / 60.00s | [46500, 76500] / 30.00s |

A synthetic 4-hour VOD with 11,076 transcript segments and 70 candidates ran in **0.4209 seconds median** for the new algorithm versus **0.0731 seconds median** for the old implementation (five runs on the local Python 3.13.5 environment). The new algorithm performs local validation and sorting and is measurably slower, but does not decode media or allocate a large audio buffer. These figures are environment-dependent; they are NOT end-to-end pipeline timings or quality improvements.

## Alternatives considered

- **Always use a fixed anchor-centered N-second interval:** cheap but can cut setup and aftermath, disregards transcript turns.
- **Expand through all nearby ASR utterances:** the old implementation; over-expands continuous conversation and can include unrelated speech.
- **Choose the largest audio spike as payoff:** rejected; volume is often unrelated to the meaningful moment.
- **Semantic LLM, diarization, frame-level video understanding:** deferred because unavailable in the current baseline, would violate cost and section boundaries, and needs separately evaluated evidence.
- **Full word-level VAD / forced alignment:** potentially better sentence cut placement but would add processing/dependencies and requires quality benchmarking.

## Limitations and review checklist

- ASR segment times are approximate; segment boundaries can split sentences. Long, overlapping or incorrectly timed segments can force an explicit hard cut. The algorithm cannot infer story-level setup, punchline, or conversational relevance. Silence and turn gaps are timing cues only.
- A quiet nonverbal payoff is not recognized from text; the algorithm retains the discovery anchor and falls back to timing. Optional audio input only nudges fallback edges toward quiet bins, and a quiet game or low-volume voice may make that misleading.
- In some clips a 6-second gap may connect unrelated lines, while a meaningful setup more than 12 seconds before the event may be excluded. The hard bounds intentionally prefer shorter reviewable clips. Reviewers can manually adjust these trade-offs.
- Clips near the source edges may be shorter than the requested minimum because the recording is shorter. If `min_length_ms` exceeds the 34-second soft cap, the explicit minimum takes precedence up to `max_length_ms`.
- No human-annotated source/VOD benchmark, perceived quality rating, Windows run, or Python 3.11 run is claimed by the local synthetic fixtures. Validate boundary coherence and subtitle timing on actual authorized source videos before accepting Section 6.
