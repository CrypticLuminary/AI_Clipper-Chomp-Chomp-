# Chomp-Chomp EDIT-01 session handoff

- Run date: 2026-11-16 Asia/Katmandu. Implementation and sampled-frame review by GPT-6.1 Sol in Codex.
- Implementation branch: work/edit-01-director. Baseline SHA: 0f159e0bf460b7baf1c0cee88e15d1db584bd07f. Use git rev-parse HEAD for the checked-out revision.
- Scope: provider-neutral evidence and editorial plan contracts, one bounded read-only Codex adapter, matched source-window evaluations, and one manually mapped demonstration. The follow-up adds optional source-clock sound cues to manual exports and revisions.
- Changed files: chomp/director, chomp/sound.py, chomp/cli.py, chomp/manual.py, chomp/clipwork.py, chomp/media.py, pyproject.toml, .github/workflows/ci.yml, tests/test_director.py, tests/test_sound_cues.py, docs/editorial-director.md, docs/shorts-finishing.md, docs/shorts-layout.md, README.md, and this handoff.
- Source D01: user-supplied Emiru VOD for private EDIT-01 evaluation, development split. SHA-256 b501192d8863d3534c281f07ca939c0ff24044c599f52f1be84e95e864c1a209. The complete file is 5,335,583 ms, 1920 × 1080 H.264 at 60 fps, with one stereo AAC track. Audio track 0 is the mixed source. Sidecar chat was unavailable.
- Environment: Windows 11 Home build 26200, CPython 3.13.14, FFmpeg 9.0.2 full build, faster-whisper 1.2.1, CTranslate2 4.9.0, and jsonschema 4.26.0. Baseline used already cached Whisper small CPU weights. The CUDA attempt failed because cublas64_12.dll was unavailable.
- Model: codex-cli 0.169.0-alpha.11.1, gpt-6.1-sol, medium reasoning, existing ChatGPT sign-in. Eight completed calls across four cases. An earlier unsupported gpt-6 attempt is recorded separately. Request hashes, timeouts, exact image IDs, CLI usage and per-mode plan hashes are under outputs/edit-01/paired. Prompt editorial-director/1 has SHA-256 34b0cee73ee30251e465fb812fd3436959526a8a9947b6eb5a8712a7000dd2be.

## Confirmed results

Paths here are relative to the worktree.

- OBSERVED: the unmodified baseline ran over the complete D01 source in 268.436 seconds and produced 60 proposals and three review previews. Its ledger and transcript are under outputs/edit-01/baseline-d01/d18cdceab6bc1c161cde6d23. None of the four nominated anchors appears inside a baseline proposal or final interval. This is a check on the nominated windows.
- OBSERVED: read local sample frames at five-minute checkpoints through 85:00, denser frames around four candidate windows, and three samples of each supplied reference Short. The comparison holds transcript, audio measurements, chat availability, candidate and prompt fixed. Frame-assisted requests add seven or eight decoded stills and their region annotations.
- OBSERVED: all eight decisions passed schema and independent source-aware validation. The manifest outputs/edit-01/verified-comparisons.json lists paired outcomes and usage.

| Window | Anchor (source ms) | Bundle SHA-256 | Text | Frames |
| --- | ---: | --- | --- | --- |
| Game loss and reaction | 898000 | a5dcbef53dd205862850cb56502b26251bf4459a021fdad44f3c0d0a17c84749 | select | select |
| Gate cutscene | 1504000 | ba029fbb6cfba8d035f79330a157258e18053130f8baa6a4e44a8e8fe4fdf9f8 | abstain | reject |
| Cannon payoff | 3039000 | 7efd72dc83de00df338ce8253f59df2eaf80508ca5ab5af52a960f62bb2f81b0 | abstain | select |
| Routine riding | 4200000 | da9c54971b52f905322bc867936b5fa3cf6b2c4a86d8bf4aa0744dd46d8f3c39 | reject | reject |

- OBSERVED: ran the ordinary pipeline on the earlier non-Emiru development recording. It completed with 12 proposals and two review plans. This recording has been used for prior development.
- OBSERVED: 261 local tests passed. The sound-cue integration test measured both the source tone and the inserted tone in the export, then trimmed the clip and checked the cue's remaining duration and asset hash.
- OBSERVED: the one D01 demonstration used the cannon story and an operator-reviewed crop plan. Its interval is 3,028,000–3,042,133 ms. Source cuts at 3,036,300 and 3,037,666 ms and the exit cut are recorded with the mapped layout in outputs/edit-01/demo/cannon-execution.json. The H.264/AAC export is 1080 × 1920 at 60 fps and passed full FFmpeg decode. Sampled export frames show the camera, cannon, ship fire, and native game subtitles. The video and edit plan are under outputs/edit-01/demo/manual-e9bc154bad0752e7c770205b.
- INFERRED: the additional frame evidence gives reviewers a more specific visual account in the game-loss and cannon windows. A matched human rating is needed to measure preference.

## Reproduce

With the same local source, cached weights, FFmpeg build, and the director extra installed, set the PowerShell variable $vod to the D01 path and run these commands from the worktree.

~~~powershell
chomp --input $vod --output 'outputs/edit-01/baseline-d01' --rights-ref 'user-supplied for private EDIT-01 testing' --acknowledge-permission --transcribe --asr-model small --asr-device cpu --layout shorts --profile preview --render
chomp director validate --source $vod --bundle 'outputs/edit-01/evidence/cannon/evidence.json' --plan 'outputs/edit-01/paired/cannon/frames/plan.json'
chomp director compare --source $vod --bundle 'outputs/edit-01/evidence/cannon/evidence.json' --out 'outputs/edit-01/paired/cannon' --model gpt-6.1-sol
pytest -q
~~~

The comparison output paths refer to saved attempts. Use a new output directory for a new model run. Other cases use the same command with the corresponding evidence and output directories. The demo uses the existing manual renderer with its saved layout, title The Ship Goes Up in Flames, balanced audio profile, and final output profile. The local review index lists paths to each plan, evidence and score template.

## Known uncertainty and next action

- NOT TESTED: continuous independent human playback, human preference scores, and an unseen creator's unedited VOD. The reference Shorts are style examples. Keep the unseen source for a separate holdout.
- NOT TESTED: final listening review. The isolated ASR pass returned an unsupported one-word cue in the cannon window. The demo retains the original audio and native game subtitles. Its review remains pending.
- The model's estimated shot time required source-frame verification. The render uses the verified cuts and a tighter lead-in, with the source sound chosen for the payoff. The editor selects optional cue assets.
- Next action: have a human rate the four neutral A/B packets in outputs/edit-01/paired/<case>/blind-review against the original VOD. Enter the ratings before opening the mode mapping and verify the cannon export's audio and native subtitles during playback.
