# Chomp-Chomp — Task and model attribution protocol

## Why

Several coding agents work in parallel. A GitHub commit identifies the GitHub account, **not** which AI model or chat produced the implementation. An agent's self-report is useful provenance but is **not cryptographic proof**. Never infer model authorship from commit messages or branch names retrospectively.

## Unique identifiers

- Task code and codename are stable across revisions: `GATE-01 / Watchtower` (CI), `FRAME-02 / Portrait` (media), `VOICE-03 / EchoMap` (diarization), `SCOUT-04 / Radar` (discovery), `JUDGE-05 / Lens` (semantic ranking), `CUT-06 / Cutline` (boundaries), `EVAL-07 / ClipLedger` (human review and measured clip quality). New tasks get a new code, even when they touch an existing section; example `VOICE-03-FIX01 / ModelPaths`.
- Agent/model code identifies a **specific session/role**, not just a company: `GPT6-REVIEW`, `GPT6-DEV`, `OPUS46-AG`, `GFLASH38-AG`, `ARGON-GEM`, `GLM53`.
- For multiple sessions of the same model, append a unique suffix, e.g. `GFLASH38-AG-B`.
- A task may have several contributors or handoffs; keep all contributor codes and their scopes in chronological order. Do not claim the whole branch came from one model when responsibility changed.

## Required in every future coding-agent prompt

1. Begin by declaring **Task ID, task codename, agent/model code, app/workspace, base branch, working branch and assigned files**.
2. Create a dedicated `docs/tasks/<TASK-ID>.md` manifest on your own branch and keep it current.
3. Use a distinct branch; preferred new naming: `feat/<task-lower>-<agent-lower>-<purpose>`. Do not rename active existing branches without coordination.
4. PR title starts `[<TASK-ID>][<AGENT-CODE>]` and targets the agreed base. If a task changes hands, include both model codes in the PR body and manifest.
5. Don't edit the top-level README registry from parallel branches: overlapping README edits cause conflicts. The reviewer updates it centrally after verifying the PR metadata.
6. Commit only your assigned files. Do not merge or mark a review gate complete yourself.
7. Return tests actually run, environments, unverified assumptions, blocked model downloads, and any proposed shared-file changes.

## Copyable task manifest

Editing tasks use the [editing regression index](editing/regressions.md) and [engineering roadmap](editing/engine-plan.md). For ShotComposer and VisualQA, include JAY3-03 in the test set and consult the SubjectLock handoff before interpreting any absence or overlay evidence.

```markdown
# TASK-ID — Task codename

- Status: in progress / review requested / accepted / changes required
- Section: ...
- Branch: ...
- Base branch and commit: ...
- PR URL: ...
- Agent/model code: ...
- App/workspace: ...
- Contributors and handoffs: ...
- Provenance confidence: self-reported / operator-confirmed / unknown
- Owned paths: ...
- Shared-path changes: ... (ideally none)

## Objective
...

## Implemented
...

## Evidence
- Test command and result: ...
- Cross-platform CI status: ...
- Real-footage/manual validation: ...

## Known failures and limitations
...

## Reviewer decision
Pending independent review.
```

## Current branch attribution

| Branch | Task | Attribution | Notes |
| --- | --- | --- | --- |
| `feat/stage1-speaker-diarization` | `VOICE-03` | **Unverified** | User described an Opus 4.6 → Gemini 3.8 Flash handoff, but code/commit ownership within the branch is not established. |
| `feat/stage1-clip-boundaries` | `CUT-06` | **User-reported GPT chat; exact model unverified** | 4/4 CI passed; real-VOD review pending before merge. |
| `feat/eval07-gpt6-dev-clipledger` | `EVAL-07` | **Assigned to GPT coding chat; branch not yet created** | Implement offline human-review evaluation tooling, separate from clip selection. |

**Review decisions:** Keep implementation approval independent from claimed model identity. Passing CI verifies certain tests, not real model accuracy or clip quality. Never mark an unknown model as confirmed merely because it shares the owner's GitHub credentials.
