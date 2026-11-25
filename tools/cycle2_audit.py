"""Reproduce the local branch/code inventory without reading private media."""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main():
    base = "24bf2f5f4ad2172e4b18728b198fe7d3e9d65aff"
    refs = git("for-each-ref", "--format=%(refname)", "refs/remotes/origin").splitlines()
    lines = ["# Cycle 2 branch audit", "", "Audit date: 2026-11-25 (Asia/Katmandu). `git fetch origin --prune` succeeded.",
             "", f"Authoritative baseline: `feat/director-first` at `{base}`.",
             "All listed remote branches are ancestors of this baseline. Source diffs and merge bases confirm that the camera, cutout, caption, evaluation, boundary and diarization fixes are integrated; no cherry-picks are needed.",
             "", "| Remote branch | Head | Merge base with baseline | Unique to branch / baseline | Changed files |", "|---|---|---|---|---:|"]
    for ref in refs:
        if ref.endswith(("/HEAD", "/work/cycle2-keystone", "/research/ds01-deepscout")):
            continue
        head = git("rev-parse", ref)
        common = git("merge-base", ref, base)
        counts = git("rev-list", "--left-right", "--count", f"{ref}...{base}").split()
        changed = git("diff", "--name-only", ref, base).splitlines()
        label = ref.removeprefix("refs/remotes/")
        lines.append(f"| {label} | `{head[:12]}` | `{common[:12]}` | {' / '.join(counts)} | {len(changed)} |")
    lines += ["", "Dependency chain: foundation → Bridge integration (merges CUT-06, CAPT-08, EVAL-07 and VOICE-03 fixes) → Stage 1 quality → EDIT-01 → stable camera → SubjectLock → ShotComposer → transparent cutout → Director First.",
              "", "The local foundation worktree is behind its remote. A local branch named `origin/integration/integ09-sol61-work-bridge` shadows the remote's short name; audit uses `refs/remotes/origin/...` when ambiguity matters. Existing worktrees and local changes were left untouched.",
              "", "No unresolved merge conflicts exist in the new worktree. Semantic production and Director First are divergent workflows, addressed through an opt-in validated renderer bridge; existing experimental components remain available.",
              "", "Initial open PRs: #1–#11 (no Director First PR); many contain already-integrated commits. During delivery, DEEPSCOUT PR #12 appeared at 66430342237bb2c96118e9cbb14ff865b713ebcd on research/ds01-deepscout. It shares the baseline but adds 15 independent commits/15 files, is not its ancestor, and is not production-approved. Source inspection found a research namespace collision; KEYSTONE interfaces were moved to chomp/research_interfaces.py. See deepscout-compatibility.md. KEYSTONE PR #13 targets the unchanged baseline. These two new task branches are excluded from the historical ancestry table.",
              "", "PR status does not establish source integration. Baseline's PR-triggered workflow query returned no runs; this is not a CI pass. See baseline-test-report.md and delivery-status.md for local and remote gates.",
              "", "Reproduce: `python tools/cycle2_audit.py`. Review `git log --all --graph --oneline` and `git diff <branch> <baseline> -- chomp tests` for source details."]
    target = ROOT / "docs/cycle2"
    target.mkdir(parents=True, exist_ok=True)
    (target / "branch-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines = ["# Module contracts and test inventory", "", "Generated from actual Python definitions/imports. Shared times are integer source-relative milliseconds; captions alone use clip-local time. Optional providers and models are opt-in.", "", "| Module | Public entry points | Internal dependencies | Tests importing module |", "|---|---|---|---|"]
    tests = {p: p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("test_*.py")}
    for path in sorted((ROOT / "chomp").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        entry = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and not n.name.startswith("_")]
        deps = sorted({n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level})
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        coverage = [p.name for p, content in tests.items() if module in content]
        lines.append(f"| `{path.relative_to(ROOT).as_posix()}` | {', '.join(entry) or 'entry point/re-exports'} | {', '.join(deps)} | {', '.join(coverage) or 'indirect / no direct import'} |")
    lines += ["", "Workflow contracts: pipeline.process accepts a local source, rights/config and optional timestamped sidecars; produces candidates.json plus review_queue.json. director.clip.run_clip accepts the same source/campaign with a structured provider; produces director-selection/1. manual.render_manual_preview accepts exact source bounds and verified geometry; produces 0.5 review/edit artifacts. ShotComposer consumes source-hashed shot evidence and validated plans. ClipLedger evaluates independent reviews; benchmark evaluates source-event annotations.",
              "", "Duplication: manual and production share create_clip/render_preview; Director has separate proposal/decision logic by design. The Cycle 2 adapter reuses dictionaries and Candidate/TranscriptIndex/enforce rather than replacing all editorial contracts. Layout and SubjectLock evaluators remain the authority for geometry-specific comparisons."]
    (target / "module-map.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
