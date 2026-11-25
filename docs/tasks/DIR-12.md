# DIR-12 — StoryMind integration

Attribution/environment/base/branch are shared with BASE-10.md. Director selections now have an opt-in source/evidence-validated route into existing framing, captions, selected-track audio, deterministic rendering and pending human review. Heuristic and semantic production workflows remain usable. New research interfaces install no heavy models. Budgets reserve final decision capacity and expose incomplete coverage and provider failures.

```text
chomp clip source.mp4 --director heuristic --rights-ref <authorization> --transcript source-clock.json --render --acknowledge-permission
chomp render-director --report outputs/<job>/director.json --output outputs/review --acknowledge-permission
```

Existing Director dry-run behavior is still the default. `--render` is opt-in and requires explicit permission acknowledgement. Rendering a partial report additionally requires `chomp render-director --allow-partial`. Direct Python integration accepts per-candidate reviewed layout-plan paths. Titles that fail existing renderer constraints remain suggestions and receive a warning; they do not become executable content or prevent an otherwise valid cut.

Provider options: `--max-calls`, `--discovery-calls`, `--investigation-calls`, `--final-reserve`, `--provider-timeout`. No provider is called by automated tests. The real-footage replay command accepts only existing successful evaluation responses and fails on any cache miss; it never invokes a model. See ../cycle2/research-integration-contract.md for compatibility and unresolved multimodal/research work.
