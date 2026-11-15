# CAPT-08 — CaptionCraft: subtitle timing, segmentation and readability

- **Agent:** GPT6-DEV
- **Task:** CAPT-08
- **Branch:** `feat/capt08-gpt6-dev-captioncraft`
- **Target:** `feat/vod-foundation` (audited head `9d361ab0f7afdede21795535dede71cece97bae4`)
- **Owned files:** `chomp/captions.py`, `tests/test_captioncraft.py`, `docs/tasks/CAPT-08.md`
- **Status:** Draft PR; independent review and authorized-video evaluation required

## 1. Audit of existing contracts and defects

`chomp/models.py` defines `TranscriptSegment(start_ms, end_ms, text, speaker_id)`. Its timestamps are integer milliseconds **relative to the source**, not clip time; there are no word-alignment or confidence fields.

`chomp/pipeline.py` calls:

```python
subtitles, cue_count = make_srt(transcript, c.start_ms, c.end_ms, duration_ms)
```

It writes the UTF-8 SRT sidecar and records `caption_timing="segment_estimated_not_word_aligned"`. This remains correct. Tests in `tests/test_core.py` and `tests/test_pipeline.py` require the unchanged tuple return, `_chunks` lines <=28 characters, markup sanitization, clip-local timecodes and rendered export compatibility.

The old implementation emitted one 28-character line per cue, even for rapid speech, and allowed flashes as short as **80ms**. It independently emitted overlapping speakers, so their cues could overlap. It displayed **all** words from a segment even if only part intersected the exported clip. It lacked a cue hold ceiling and used character-count timing without describing its uncertainty. HTML-like controls were sanitized, but ASS backslash commands and Unicode bidi controls were not.

## 2. Implemented deterministic policy

- **Text:** collapse newline/whitespace and neutralize `< > { }`, backslash styling escapes, bidi overrides and control characters. Preserve Nepali/English/emoji, punctuation and combining marks. Split into **<=28 codepoints per line** and **<=2 lines per cue**. Break preferentially near punctuation, including Nepali `।`. Common Nepali virama+joiner combinations are kept together when a long token wraps.
- **Pacing:** target **24 codepoints per second** over each segment. An impossible transcript is **abbreviated with `…`** rather than flashed rapidly; this is intentionally lossy and not a semantic summary. Prefer full text when the duration allows it.
- **Durations:** for segment subdivision target **>=700ms** per cue; omit fragments shorter than **250ms**. A standalone 250–699ms exclamation may still show for its actual short duration rather than falsely extending beyond speech. Cap each visible cue to **6000ms**.
- **Timing:** allocate estimated within-segment times by character weights, never extend beyond the known segment end or clip/source end; keep silence gaps blank, clip-localize all integer milliseconds. This is **not** per-word forced alignment.
- **Overlaps:** sort by segment onset and let a later speaker onset preempt earlier displayed captions. Exact simultaneous onset uses stable input order, showing only the first speaker. This prevents stacked/out-of-order cues but may omit simultaneous dialogue.
- **Clipped segments:** approximate the visible prefix/suffix using character-weighted word midpoints, and mark omitted portions with `…`. Tiny clipped pieces may not produce captions. These words are *estimates*, not verified aligned text.
- **Compatibility:** public `make_srt(segments, start_ms, end_ms, duration_ms) -> (str,int)` signature and the `_chunks` helper are unchanged. No new dependency, model, media call or API.

The pipeline already labels caption timing as **segment estimated**. No existing field establishes genuinely word-aligned input, so CaptionCraft makes no claim to genuine alignment. A future explicit timing-provenance field must be proposed to the owner of the shared models and pipeline, **not added by this task**.

## 3. Before/after synthetic measurements

Original `feat/vod-foundation` implementation was copied verbatim into a local comparison harness; identical fixtures were executed against CaptionCraft with Python **3.13.5**.

| Fixture | Original | CaptionCraft |
| --- | --- | --- |
| Fast 50-word utterance, 1.6s | 16 cues, shortest **96ms** | **1 cue, 1600ms**, deliberately abbreviated |
| Three overlapping speakers | 6 cues, **2 overlapping pairs** | **3 cues, 0 overlaps** |
| Two clip-truncated speech segments | 2 cues, full segment text | **2 cues**, estimated partial text with ellipses |
| Long 80s ASR segment, 70 words | 35 cues | **18 cues** |
| Short 1.1s reaction | 1 cue, 1100ms | 1 cue, 1100ms |

Time-only performance microbenchmark, synthetic 40-minute transcript (1200 utterances), 70 clip windows, median of five runs: **0.0442 seconds** old vs **0.0731 seconds** CaptionCraft. New formatting is modestly slower; this is not an end-to-end VOD benchmark. It adds no media decoding or outside dependencies.

### Exact SRT example: clipped boundary

Clip is source 5s–12s; utterances originally span 3s–8s and 9s–13s. Original:

```srt
1
00:00:00,000 --> 00:00:03,000
Setup ‹something›

2
00:00:04,000 --> 00:00:07,000
then a fun payoff!

```

CaptionCraft:

```srt
1
00:00:00,000 --> 00:00:03,000
… ‹something›

2
00:00:04,000 --> 00:00:07,000
then a fun …

```

The partial words in this illustration are **approximated by text position**. They are not guaranteed to match the real speech and must be checked on actual footage.

## 4. Verification

From the repository root:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q tests/test_captioncraft.py
python -m pytest -q -ra
```

The caption-specific tests cover short/long/fast/slow speech, overlapping and same-onset speakers, silence gaps, source start/end, clip truncation, Unicode and Nepali conjuncts, punctuation, markup injection, 80s ASR segments, exact SRT ordering, all existing interface contracts, invalid timestamps and **200 seeded randomized source-timing cases**. Optional synthetic FFmpeg integration creates a 3-second test video and uses the **existing `chomp.media.render_preview`**, under folder/file paths containing spaces. It requires an environment with the package and FFmpeg/ffprobe and otherwise skips.

Initial isolated local run: **22 passed, 1 skipped**; the skipped item was the optional renderer test because the local working mirror contained only the caption and model modules. The CI matrix in the full repository is authoritative for FFmpeg integration and Windows/Python compatibility.

**Verified full-repository GitHub Actions push run:** [CI #43612981921](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/actions/runs/43612981921) completed **successfully** on Windows and Ubuntu, each with Python 3.11 and 3.13. The Ubuntu Python 3.13 log reports **46 tests passed in 7.04 seconds**, including the synthetic FFmpeg caption-rendering test. The other three jobs also passed. Earlier CI failed because the *test fixture*, not the caption algorithm, named the SRT outside the renderer's `candidate_*.srt` convention; that fixture was corrected without altering media code. These results verify mechanical integration, not real footage readability.

## 5. Known limitations / independent reviewer focus

- **No true word alignment:** char-weighted time estimates may be inaccurate, especially at clipped segment boundaries or after ASR timestamp drift.
- **Lossy extreme speech:** excessive text is replaced with a shorter prefix and ellipsis; this can lose important information. No AI semantics are used to decide what to omit.
- **Overlap information loss:** simultaneous speech is serialized; exactly simultaneous onsets choose first input, and earlier overlapping utterances may be truncated at the next onset.
- **Unicode glyph geometry:** the character limit is a codepoint estimate, not screen width or font size. Grapheme splitting handles common Nepali marks but is not a full UAX #29 implementation; visual glyph coverage depends on libass/fonts.
- **Long sparse ASR segments:** a few words may appear for up to 6s, then disappear while the nominal ASR segment continues; there is no evidence for more precise placement.
- **Tiny true utterances:** 250–699ms captions can still be brief because artificially extending them beyond declared speech would be misleading.
- **No compositor changes:** font size, baseline, outlines, safe areas, and facecam placement require a separately owned rendering PR.
- **Synthetic tests only validate mechanics:** human reviewers must watch authorized exports to judge actual subtitle timing, readability, Nepali glyphs and synchronization. Do not claim perceptual improvement without such evaluation.

**Integration change request (separate):** optional future explicit `timing_provenance` field for true word-aligned transcript input, and later a renderer style audit. No shared or unrelated file was changed by this task. Do not self-approve or merge.
