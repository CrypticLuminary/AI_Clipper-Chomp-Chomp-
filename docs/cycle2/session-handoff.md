# KEYSTONE session handoff

- Run date: 2026-11-25 Asia/Katmandu. Tasks BASE-10, BENCH-11, DIR-12. Attribution: BASE-10.md.
- Baseline: feat/director-first at 24bf2f5f4ad2172e4b18728b198fe7d3e9d65aff. Working branch: work/cycle2-keystone. Code checkpoint: 734aa37e1c8ba8d677869e6907b182e36cf67147; final head: `git rev-parse work/cycle2-keystone`.
- Confirmed: baseline 323 tests; final 355 tests; fifteen synthetic smoke cases; actual source-bound audio checks and three private real exports with zero new provider calls. Details and limitations are in final-engineering-report.md.
- Implemented: compatible benchmark/selection reader, opt-in verified exports, phase budgets and recovery, VFR media fix, provider-neutral evidence/retrieval/lane interfaces. Research algorithms and independent quality validation remain external gates.
- Local review: outputs/cycle2-private-speed-v2/private-review.json and V01/V02 review pages. Footage, transcripts and comparison PNGs stay ignored/private. Human playback, ratings and approval remain pending.
- Reproduce: activate installed environment; run `python -m pytest -q -ra` and `python -m chomp.evaluation.smoke --out outputs/new-directory`. All automated paths use synthetic/fake providers. See chompbench-manual.md for review/benchmark commands.
- Next: review source/output comparisons and listen to the private clips; DEEPSCOUT should read research-integration-contract.md and coordinate shared-file edits. Do not tune against holdout annotations, publish footage, merge main or claim subjective improvements from technical tests.
