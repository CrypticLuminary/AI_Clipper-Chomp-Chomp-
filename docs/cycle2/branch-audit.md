# Cycle 2 branch audit

Audit date: 2026-11-25 (Asia/Katmandu). `git fetch origin --prune` succeeded.

Authoritative baseline: `feat/director-first` at `24bf2f5f4ad2172e4b18728b198fe7d3e9d65aff`.
All listed remote branches are ancestors of this baseline. Source diffs and merge bases confirm that the camera, cutout, caption, evaluation, boundary and diarization fixes are integrated; no cherry-picks are needed.

| Remote branch | Head | Merge base with baseline | Unique to branch / baseline | Changed files |
|---|---|---|---|---:|
| origin/feat/capt08-gpt6-dev-captioncraft | `515c2d9d940e` | `515c2d9d940e` | 0 / 45 | 113 |
| origin/feat/director-first | `24bf2f5f4ad2` | `24bf2f5f4ad2` | 0 / 0 | 0 |
| origin/feat/edit-fx03-shotcomposer | `4acbd67aff25` | `4acbd67aff25` | 0 / 3 | 21 |
| origin/feat/eval07-gpt6-dev-clipledger | `8989333d9f88` | `8989333d9f88` | 0 / 42 | 110 |
| origin/feat/stage1-clip-boundaries | `82ad6d4dd61d` | `82ad6d4dd61d` | 0 / 52 | 110 |
| origin/feat/stage1-speaker-diarization | `804a28c09410` | `804a28c09410` | 0 / 56 | 112 |
| origin/feat/vod-foundation | `c8718a1ef856` | `c8718a1ef856` | 0 / 46 | 114 |
| origin/fix/fx03-transparent-cutout | `bf0ff59ceb55` | `bf0ff59ceb55` | 0 / 1 | 14 |
| origin/fix/voice03-fix01-gflash38-ag-modelpaths | `d713e7532e8c` | `d713e7532e8c` | 0 / 55 | 110 |
| origin/fix/voice03-fix02-gflash38-ag-identityguard | `4f5b78b4aafd` | `4f5b78b4aafd` | 0 / 55 | 111 |
| origin/fix/voice03-fix03-gflash38-ag-turnsguard | `ba300264d949` | `ba300264d949` | 0 / 54 | 110 |
| origin/integration/integ09-sol61-work-bridge | `1e7e9222cd8e` | `1e7e9222cd8e` | 0 / 12 | 98 |
| origin/main | `a5eeb4abcf12` | `a5eeb4abcf12` | 0 / 66 | 120 |
| origin/work/edit-01-director | `f187fd107ee0` | `f187fd107ee0` | 0 / 10 | 55 |
| origin/work/edit-fx-01-stablecamera | `220acdba0b4e` | `220acdba0b4e` | 0 / 8 | 50 |
| origin/work/edit-fx-02-subjectlock | `8dd1cb52634c` | `8dd1cb52634c` | 0 / 5 | 42 |
| origin/work/stage1-quality | `0f159e0bf460` | `0f159e0bf460` | 0 / 11 | 69 |

Dependency chain: foundation → Bridge integration (merges CUT-06, CAPT-08, EVAL-07 and VOICE-03 fixes) → Stage 1 quality → EDIT-01 → stable camera → SubjectLock → ShotComposer → transparent cutout → Director First.

The local foundation worktree is behind its remote. A local branch named `origin/integration/integ09-sol61-work-bridge` shadows the remote's short name; audit uses `refs/remotes/origin/...` when ambiguity matters. Existing worktrees and local changes were left untouched.

No unresolved merge conflicts exist in the new worktree. Semantic production and Director First are divergent workflows, addressed through an opt-in validated renderer bridge; existing experimental components remain available.

Initial open PRs: #1–#11 (no Director First PR); many contain already-integrated commits. During delivery, DEEPSCOUT PR #12 appeared at 66430342237bb2c96118e9cbb14ff865b713ebcd on research/ds01-deepscout. It shares the baseline but adds 15 independent commits/15 files, is not its ancestor, and is not production-approved. Source inspection found a research namespace collision; KEYSTONE interfaces were moved to chomp/research_interfaces.py. See deepscout-compatibility.md. KEYSTONE PR #13 targets the unchanged baseline. These two new task branches are excluded from the historical ancestry table.

PR status does not establish source integration. Baseline's PR-triggered workflow query returned no runs; this is not a CI pass. See baseline-test-report.md and delivery-status.md for local and remote gates.

Reproduce: `python tools/cycle2_audit.py`. Review `git log --all --graph --oneline` and `git diff <branch> <baseline> -- chomp tests` for source details.
