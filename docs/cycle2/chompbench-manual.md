# ChompBench-1 manual

ChompBench-1 extends `chomp.evaluation.benchmark` and ClipLedger. It accepts `chomp-candidates/1` or `director-selection/1`; it does not create a competing annotation system. Reports retain `chomp-event-benchmark/1` with additive fields. Old source-event files remain valid.

Create a reproducible technical development set (Windows or Linux, with the project installed and FFmpeg/FFprobe on PATH):

```text
python -m chomp.evaluation.smoke --out outputs/chompbench-new
python -m chomp.evaluation dataset-validate examples/chompbench/dataset.json
python -m chomp.evaluation events-validate examples/chompbench/events-template.json
python -m chomp.evaluation benchmark --events outputs/chompbench-new/landscape_speech/events.json --candidates <director.json-or-candidates.json> --out outputs/report.json --markdown outputs/report.md
python -m chomp.evaluation compare-experiments --config outputs/chompbench-new/landscape_speech/matched-experiment.json --out outputs/comparison.json
```

Choose new output paths; template/report creation never overwrites annotations or existing reports. Add `--technical <technical.json>` for source/run-bound decoded measurements, and `--manifest <review_queue.json> --reviews <independent-clipledger.json>` for actual independent clip judgments. Use the existing `template`, `validate`, `report` and `compare` commands for ClipLedger reviews.

The smoke run creates fifteen cases, per-case source/transcript/Director decisions, scripted source-geometry fixture plans where applicable, clip captions/edit files, decoded diagnostics, contact sheets, source-event annotations, and JSON/Markdown reports. Colored webcam/face markers and scripted transcripts establish technical contracts only. Speech uses FFmpeg flite when installed; otherwise the manifest explicitly labels a tone surrogate. This is not a semantic audio classification or real face-identification benchmark. Two synthetic cases exercise a separate holdout registration group; this is split-validation mechanics, not evidence of unseen-creator generalization.

The first case runs A (existing heuristic process), B (existing semantic process with a deterministic fake judge), C (Director First with a fake structured provider), and D (the same accepted Director output through the production bridge). All use the same source, events, rights reference and protocol. The descriptor records provider limits, candidate limits, inspected duration and modalities; unknown budgets are null. Differences are reported, never silently declared matched. D reuses C's editorial analysis, so it tests downstream integration rather than providing a new editorial algorithm. Processing times are recorded as observed, not as a matched hardware efficiency study.

For real comparisons, run the existing workflows with explicitly configured available local providers and produce the same descriptors. This harness never invokes providers itself. No API access or paid infrastructure is needed by the automated suite. Human quality metrics stay null until independent reviews are supplied.

The long-term dataset target is 24 authorized sources across single-player gaming, multiplayer gaming, reactions, Just Chatting, multiple speakers and difficult compositions. The committed dataset is an **empty registration template**, not 24 fabricated VODs. Register actual authorized sources externally or under gitignored `media/`; commit only appropriate metadata. Assign development/holdout at creator level before annotation. `dataset-validate` rejects creator leakage and duplicate sources. No tuning on holdout annotations is implemented or authorized by these tools.

Source IDs are content identities, not creator identities. Keep a private media-path map and explicit rights references. Annotation and comparison tools reject source/clock/run mismatches. Historical report migration to production requires a hash-matched transcript snapshot or the original hashed transcript sidecar. Reports lacking both cannot be rendered through the bridge; rerun analysis with a supplied transcript or validated local cache.
