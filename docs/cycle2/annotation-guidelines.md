# Independent source annotations

Watch the authorized source independently of generated candidates. Record every interesting event in the assigned source scope, including quiet or non-dialogue events. Do not mark a source complete until the full recording has been watched and pending/disputed event labels resolved. A section review is incomplete for full-source recall. A complete source with no interesting events has an empty events array.

Use `annotation_origin: human` only for independent human judgments. Machine suggestions use `ai` and cannot establish complete independent annotations. Generated technical examples use `synthetic`; their metrics must always be identified as synthetic. `reviewer` and per-event `reviewer_id` must identify the actual reviewer or explicitly labeled synthetic generator. Do not copy model reasons into purported independent judgments.

All times use integer **source-relative milliseconds**, with end-exclusive spans. Never use container start timestamps or clip-local subtitle offsets. Set the core to the event's decisive portion, essential span to all necessary setup/development/payoff, and admissible edge ranges to acceptable opening and closing positions. Essential must contain core, latest admissible start cannot pass essential start, and earliest admissible end cannot precede essential end. Point-valued admissible edges are permitted.

| Requested information | Existing/additive field |
|---|---|
| Source, creator, category, duration, rights, split | `source_id`, `creator_id`, `content_category`, `duration_ms`, `rights_reference`, `dataset_split` |
| Annotation completeness | existing `complete` plus matching `annotation_completeness` (`complete`/`incomplete`) |
| Event identity and description | reuse `id` for event_id; add `event_description`, `highlight_category` |
| Core and essential start/end | reuse `core_ms: [start,end]`, `essential_ms: [start,end]` |
| Acceptable starts/ends | reuse `start_range_ms`, `end_range_ms` pairs |
| Necessary evidence | `required_audio_evidence`, `required_visual_evidence` string arrays; describe observable requirements rather than inventing observations |
| Meaning and review | `necessary_context`, `viewer_value`, `reviewer_id`, `review_status` (`pending`/`reviewed`/`disputed`) |

This is an additive migration within `chomp-source-events/1`; span fields are not renamed or duplicated. `benchmark_name: ChompBench-1` opts into stricter metadata validation. Legacy files without that marker retain their old validation rules and report `annotation_origin: unspecified_legacy`.

Register the creator split before viewing holdout candidate proposals. Keep holdout annotations inaccessible to threshold/prompt tuning. Source-level labels establish recall and completeness; selected-clip ClipLedger reviews establish approval precision. They are different evidence. Missing modalities and unknown visual identities stay unavailable. Rights metadata records authorization; it is not a substitute for obtaining it.
