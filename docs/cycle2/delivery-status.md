# KEYSTONE delivery status

Date: 2026-11-25 Asia/Katmandu. Branch `work/cycle2-keystone`; base `feat/director-first` at `24bf2f5f4ad2172e4b18728b198fe7d3e9d65aff`.

- Pushed implementation/test head: `6b08b03def83238dd84305599b759f9c91bd8144`. Delivery documentation follows that code revision; use `git rev-parse work/cycle2-keystone` for the exact delivered documentation head.
- [Draft PR #13](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/13) targets `feat/director-first`. No merge, auto-merge, force push, branch deletion or footage publication occurred. Only this designated branch was pushed. Local milestone tag `cycle2-base10` points to initial code checkpoint `734aa37` and was not pushed.
- Final local suite on the delivery code head: **355 passed in 48.63 seconds**, zero failures/skips. Dependency health, compileall, schema/registration validation and whitespace checks passed.
- Complete synthetic matrix: **15/15 passed**; fourteen unique generated sources registered, with separate synthetic development/holdout groups. This is technical split mechanics, not an unseen real-creator evaluation.
- [CI run 45638113852](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/actions/runs/45638113852), exact code head above: **all four jobs passed**, including tests and the full Cycle 2 smoke matrix.

| Platform | Python | Job | Result |
|---|---|---:|---|
| Windows | 3.13 | 139288958996 | success |
| Windows | 3.11 | 139288959556 | success |
| Ubuntu | 3.13 | 139288959492 | success |
| Ubuntu | 3.11 | 139288959268 | success |

The first PR run, 45637183281 at 533159d, had two successful Ubuntu jobs and one pre-existing test-assumption failure in each Windows job. FFmpeg 9.1 reports a 4002ms AAC-rounded source rather than the hardcoded 4000ms encode request. The correction preserves exact source-clock round-trip and delayed-audio waveform assertions; no assertion was removed or failure marked expected. The updated matrix above passes.

DEEPSCOUT PR #12 was inspected at `66430342237bb2c96118e9cbb14ff865b713ebcd`. KEYSTONE's module was moved to `chomp/research_interfaces.py` to avoid shadowing DS-01's `chomp/research/` package. A temporary isolated no-commit combined checkout passed **370 tests in 53.41s**; its merge was aborted, and the research algorithm remains separate and disabled in production.

Private real-media verification: three new portrait review exports from one prior-authorized source section; decoded waveform correlation >0.9997; full decode; zero new provider calls using validated historical cache responses. All footage, transcripts, cache responses and comparison images stay local/ignored. Review packets are under `outputs/cycle2-private-speed-v2/`; human playback/quality approval is pending.

Outstanding: independent human labels and the full 24-authorized-VOD dataset; broader representative real-video/creator/category coverage; transparent-webcam composition quality; actual semantic audio/visual algorithms and non-dialogue semantic acceptance. These are not reported as completed or inferred from synthetic tests. Read final-engineering-report.md and deepscout-compatibility.md for the precise limits and next extension steps.
