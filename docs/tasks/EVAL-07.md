# EVAL-07 — ClipLedger human highlight evaluation

- **Agent:** GPT6-DEV
- **Task:** EVAL-07
- **Branch:** `feat/eval07-gpt6-dev-clipledger`
- **Target:** `feat/vod-foundation` (base audited at `c043890557f6b1b7911cb761a316deb35e8292ae`)
- **Status:** draft implementation; independent review required
- **Scope:** selected-clip human evaluation and same-source comparisons, not discovery/ranking/rendering
- **Dependencies:** Python 3.11+ standard library; existing pytest for automated tests

## 1. Audited pipeline contract

Current `review_queue.json` is schema `0.4` and contains `job_id`, `source` (the original path), `duration_ms` (source-relative integer milliseconds), `candidate_count`, `selected_for_review` and an ordered `clips` array. Each selected clip contains `candidate.start_ms`, `candidate.end_ms`, `candidate.anchor_ms`, optional `preview` (often null when render is disabled), `edit_spec`, subtitle metadata, generated ranking scores and review/publishing statuses. The manifest is not itself a human annotation file.

**Important missing fields:** no durable source-content hash, no semantic event ID, no calibrated quality prediction and no human quality label. Neither a review filename nor a candidate MP4 filename identifies a common event across versions. `job_id` is a pipeline setting/media-path fingerprint, not an event identity.

ClipLedger reads the existing manifest **without modifying the pipeline**, uses its ordered selected clips as the evaluation universe, and derives an ID from SHA-256 of `[job_id, source path, start_ms, end_ms, anchor_ms, duplicate-occurrence index]` (first 24 hex digits). It also emits `selected_001`, `selected_002`, etc. as user-friendly position identifiers. IDs survive renaming previews or changing heuristic scores; if the underlying timestamps or job ID change, new IDs are deliberate. Within-job duplicate spans receive separate IDs. The label file binds immutable identity fields back to the manifest and rejects changes, omissions and duplicate IDs.

## 2. Recommended review workflow

### Create a protected review template

From the repository root:

```bash
python -m chomp.evaluation template "path with spaces/review_queue.json" --out "path with spaces/clipledger-reviews.json"
```

The program uses exclusive file creation. Re-running `template` against the same valid ledger **preserves human entries byte-for-byte**, returning `created: false`. If the file does not match the current manifest, it refuses to overwrite and gives an error. Keep each review file under version control or backed up separately from pipeline output, which can be regenerated.

**Same source on different paths (for later comparison):** use the same explicitly verified identifier during both template creations, e.g. `--source-id recording-2026-11-14-sha256-confirmed-externally`. Without this, the source path string is the default source ID. That override is a *human assertion* about source identity, not independent media hashing.

### Human review

Open the annotation JSON and edit only the human fields, **not** job, clip, timing, preview or source identity fields. Watch the real MP4 or authorized original recording. When no preview exists, the file is still valid; report counts the preview as `missing` or `not_rendered`. Neither condition creates an automatic `bad` decision.

Use the following rubric for each selected clip:

| Field | Allowed values / meaning |
| --- | --- |
| `decision` | `"good"` (interesting and coherent enough to keep), `"bad"` (not suitable), `"uncertain"` (insufficient confidence), or `null` (unreviewed) |
| `reviewer_id` | Non-empty human reviewer identifier whenever `decision` is set; otherwise can be empty |
| `hook_quality` | Integer 1–5, weak opening to strong attention hook |
| `context_completeness` | Integer 1–5, missing setup to clear standalone setup |
| `payoff_completeness` | Integer 1–5, cut/missing aftermath to complete payoff |
| `audio_correct` | `"yes"` / `"no"` / `"n/a"` / `null` |
| `visual_correct` | `"yes"` / `"no"` / `"n/a"` / `null` |
| `subtitle_readable` | `"yes"` / `"no"` / `"n/a"` / `null` |
| `duplicate_content` | `"yes"` / `"no"` / `"uncertain"` / `null` |
| `failure_category` | One primary category for each `bad` decision: `uninteresting`, `weak_hook`, `missing_setup`, `missing_payoff`, `wrong_timing`, `audio`, `framing`, `subtitles`, `duplicate` or `other` |
| `notes` | Free-form optional human notes |

`null` means not assessed. `"n/a"` means that particular technical aspect is not applicable or cannot be assessed. Ratings may be partially filled while reviewing; incomplete fields are excluded individually from aggregate rating denominators. Use `uncertain` explicitly instead of guessing. There is no conversion of score or loudness features into a human label.

### Validate, report, compare

```bash
python -m chomp.evaluation validate --manifest "run-a/review_queue.json" --reviews "run-a/human-reviews.json"
python -m chomp.evaluation validate --manifest "run-a/review_queue.json" --reviews "run-a/human-reviews.json" --require-complete

python -m chomp.evaluation report --manifest "run-a/review_queue.json" --reviews "run-a/human-reviews.json" --out "reports/a.json"

python -m chomp.evaluation compare \
  --old-manifest "run-a/review_queue.json" --old-reviews "run-a/human-reviews.json" \
  --new-manifest "run-b/review_queue.json" --new-reviews "run-b/human-reviews.json" \
  --out "reports/old-vs-new.json"
```

Without `--out`, `report` and `compare` print JSON to stdout. The strict `--require-complete` flag requires a human decision and all three 1–5 quality ratings, all three technical statuses, and a duplicate status for every selected clip (zero candidates are valid). A `bad` decision always requires a failure category. Reports can replace a previous ClipLedger report only; a manifest, human ledger, or unknown JSON file is never overwritten by report generation.

On Windows, quote paths with spaces and use the same commands on a single line, or the shell's native continuation syntax rather than Bash's backslash.

## 3. Metric definitions and denominators

**Every metric is about the selected clips only.** All counts/rates come from reviewer decisions or explicit timing-only detection; a generated rank/score is never a human label.

| Metric | Definition | Missing/uncertain behavior |
| --- | --- | --- |
| Reviewed coverage | (`good` + `bad` + `uncertain` decisions) / all selected clips | Blank decision is unreviewed. Zero selected => null rate |
| Determinate coverage | (`good` + `bad`) / all selected clips | Uncertain and blank excluded from numerator |
| Precision@3 | `good` among first `min(3, selected_count)` selected slots / number of available slots | **Null** unless all those slots have determinate labels; zero slots => null. Returns slot, judged and good counts and coverage |
| Interesting rate | `good` / (`good` + `bad`) for reviewed clips | Uncertain/unreviewed excluded. Zero determinate => null |
| Technical correctness (per dimension) | `yes` / (`yes` + `no`) among decided reviews with this field judged | `n/a` and `null` excluded; counts included. Zero evaluable => null |
| Mean quality (per dimension) | Mean 1–5 among decided reviews with an actual rating | No ratings => null |
| Primary failure distribution | Category counts **among `bad` judgments** | Bad judgments require a category; report includes bad denominator |
| Reviewer-flagged duplicate rate | `duplicate_content=yes` / (`yes` + `no`) among decided reviews | `uncertain` and `null` excluded |
| Potential time-overlap duplicate rate | Number of selected clips that participate in >=1 qualifying same-run overlap pair / selected count | **Timing heuristic only**; separately reports number of pairs; zero selected => null |

**Precision@3 is a fully labeled, selected-clip measure**, not an estimate of discovery recall, true virality, or a calibrated probability. For a two-clip run it is precision of the two *available* slots, with denominator 2 rather than arbitrarily penalizing the missing third slot. Comparison delta is null if either underlying rate is unknown. For scientific use, additionally report selection volume and reviewer coverage alongside precision.

## 4. Same-source run comparison

The comparison refuses runs with different `source_id` or source durations differing by more than 1000 ms. A matching source ID/duration is not cryptographic evidence that the files are identical. Both must use an aligned source-relative clock: offsets, cuts, remux drift and unrelated files invalidate comparisons.

ClipLedger treats intervals as *possible* matching moments only when:

- shared duration covers at least **60% of the shorter clip**, and
- anchors are within **12 seconds**, **or** temporal intersection-over-union is at least **0.4**.

A small maximum-weight bipartite assignment creates one-to-one old/new pairs; candidate filenames, candidate IDs and model scores do not participate. Output lists paired clip IDs, both human decisions, temporal overlap and anchor offset, unmatched clips, and determinate good/bad transitions. Review judgments are **never copied** across versions. Similar timestamps can still correspond to different events, and an important event with radically shifted anchors may not match.

Compare measured precision, interesting rate, coverage, technical aspects, failures and duplication; inspect actual matched preview clips before concluding a quality change. Selection variation, review uncertainty, annotation disagreement and boundary shifts remain possible confounders.

## 5. Reproducible synthetic example

Generate a **fully synthetic** three-clip old/new run with editable annotation templates, explicitly labeled fake decisions, JSON reports and comparison. No media is downloaded or rendered.

```bash
python -m chomp.evaluation.demo --out "clipledger synthetic demo"
python -m chomp.evaluation validate --manifest "clipledger synthetic demo/old/review_queue.json" --reviews "clipledger synthetic demo/old/synthetic_reviews.json" --require-complete
python -m chomp.evaluation compare --old-manifest "clipledger synthetic demo/old/review_queue.json" --old-reviews "clipledger synthetic demo/old/synthetic_reviews.json" --new-manifest "clipledger synthetic demo/new/review_queue.json" --new-reviews "clipledger synthetic demo/new/synthetic_reviews.json"
```

The demo folder **must not exist** beforehand. A repeated invocation refuses to overwrite files. Scores of 99.0 in the synthetic manifest are deliberate decoys: the synthetic labels are assigned independently in the demo generator, not derived from those scores.

Example of the first *unfilled* row produced by the template generator for the old synthetic run (ID is deterministic):

```json
{
  "clip_id": "94e97071bd3feb9fcc62f38a",
  "candidate_id": "selected_001",
  "start_ms": 1000,
  "end_ms": 9000,
  "anchor_ms": 5000,
  "preview_path": null,
  "reviewer_id": "",
  "decision": null,
  "hook_quality": null,
  "context_completeness": null,
  "payoff_completeness": null,
  "audio_correct": null,
  "visual_correct": null,
  "subtitle_readable": null,
  "duplicate_content": null,
  "failure_category": null,
  "notes": ""
}
```

Synthetic example outputs (selected fields from the complete machine-generated JSON):

```json
{
  "old_report": {
    "selected_count": 3,
    "review_coverage": {
      "reviewed": 3,
      "unreviewed": 0,
      "uncertain": 1,
      "determinate": 2,
      "reviewed_rate": 1.0,
      "determinate_rate": 0.666667
    },
    "precision_at_3": {
      "value": null,
      "good": 1,
      "determinate": 2,
      "slots": 3,
      "coverage": 0.666667
    },
    "interesting_rate": {"value": 0.5, "good": 1, "bad": 1, "denominator": 2}
  },
  "new_report": {
    "selected_count": 3,
    "precision_at_3": {
      "value": 0.666667,
      "good": 2,
      "determinate": 3,
      "slots": 3,
      "coverage": 1.0
    },
    "interesting_rate": {"value": 0.666667, "good": 2, "bad": 1, "denominator": 3}
  },
  "comparison": {
    "metric_deltas": {
      "precision_at_3": null,
      "interesting_rate": 0.166667
    },
    "transitions_determinate_matched": {
      "good_to_good": 1,
      "bad_to_good": 1
    }
  }
}
```

**These are invented synthetic fixtures, not observed creator-clip results or actual human annotations.** The demonstration labels must never be submitted as an evaluation dataset.

## 6. Tests, evidence and exit criteria

Run from an editable install:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q tests/test_evaluation.py
python -m pytest -q -ra
```

The synthetic tests cover malformed review manifests, invalid/missing annotations, integer timestamp rules, immutable IDs, zero/two/three selected clips, incomplete and uncertain reviews, tri-state technical statuses, human duplicate marks versus overlap hints, same-clock old/new matching, differing source aliases, changed source durations, preservation of reviewed JSON across repeated template generation, report overwrite protections, Windows-style source paths and names with spaces, CLI subprocess calls, and the reproducible demo. Existing CI also runs offline FFmpeg integration tests across Windows/Ubuntu and Python 3.11/3.13. Passing tests confirms software contracts, **not** the accuracy of real highlight judgments.

Exit criteria for independent review: accurate denominators on partial/uncertain labels; no accidental label loss; no score leakage; no cross-source matches without explicit source ID; no edit of other agents' files; no discovery-recall claims; real human rubric study before production acceptance.

## 7. Known limits and proposed future integration

- **No real human gold set yet.** Real highlight quality and inter-reviewer agreement must be evaluated separately with approved footage and multiple blinded reviewers.
- No reliability/calibration of event overlap or cross-run matching is established. A 60% overlap can be a false match; an actual common moment without overlapping windows may be missed.
- Stream source integrity depends on operator-provided IDs; ClipLedger does not hash multi-hour recordings or align re-encoded source clocks.
- One JSON ledger is one set of selected-clip judgments. For multiple annotators, use separate copies and report inter-rater agreement externally; majority-vote ground truth is intentionally not invented.
- Only selected candidates are evaluated. Estimating discovery recall would require source-wide annotated moments and missed-event auditing.
- Missing previews are logged, not scored automatically. This tool does not probe media, run models, download footage, identify speakers or publish clips.
- No integration edits requested. Optional later pipeline change: expose source-content hash, immutable event IDs, algorithm version and reviewer-friendly links in output metadata, using a separately owned contract PR.

**Files owned by EVAL-07:** `chomp/evaluation/`, `tests/test_evaluation.py`, `docs/tasks/EVAL-07.md`. Nothing else is modified by this task.
