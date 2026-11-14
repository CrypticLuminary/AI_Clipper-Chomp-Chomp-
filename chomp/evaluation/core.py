"""Local review ledger and reproducible, *selected-clip-only* evaluation.

All source times are integer milliseconds. Human ratings are independent of
the pipeline's heuristic score. Nothing in this module estimates recall,
virality, or semantic event identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from typing import Any
import json
import math
import os
import tempfile

SCHEMA = "clipledger/1"
DECISIONS = {"good", "bad", "uncertain"}
TECH = {"yes", "no", "n/a"}
DUPLICATE = {"yes", "no", "uncertain"}
FAILURES = {
    "uninteresting", "weak_hook", "missing_setup", "missing_payoff",
    "wrong_timing", "audio", "framing", "subtitles", "duplicate", "other",
}
QUALITY = ("hook_quality", "context_completeness", "payoff_completeness")
TECH_FIELDS = ("audio_correct", "visual_correct", "subtitle_readable")
HUMAN_FIELDS = (
    "reviewer_id", "decision", *QUALITY, *TECH_FIELDS,
    "duplicate_content", "failure_category", "notes",
)
IDENTITY_FIELDS = (
    "clip_id", "candidate_id", "start_ms", "end_ms", "anchor_ms", "preview_path",
)
ANNOTATION_FIELDS = frozenset((*IDENTITY_FIELDS, *HUMAN_FIELDS))
ROOT_FIELDS = frozenset(("schema_version", "source_job_id", "source_path", "source_id", "clips"))


class ClipLedgerError(ValueError):
    """Invalid or incompatible manifest, label, or evaluation input."""


@dataclass(frozen=True)
class Clip:
    clip_id: str
    candidate_id: str
    start_ms: int
    end_ms: int
    anchor_ms: int
    preview_path: str | None

    def identity(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in IDENTITY_FIELDS}


@dataclass(frozen=True)
class Manifest:
    path: Path
    job_id: str
    source: str
    duration_ms: int
    candidate_count: int
    clips: tuple[Clip, ...]


def _object(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise ClipLedgerError(f"{where} must be a JSON object")
    return value


def _integer(value: Any, where: str, lower: int = 0) -> int:
    if type(value) is not int or value < lower:
        raise ClipLedgerError(f"{where} must be an integer >= {lower}")
    return value


def _required_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClipLedgerError(f"{where} must be a nonempty string")
    return value


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ClipLedgerError(f"Invalid JSON in {path}: {exc}") from exc


def read_manifest(path: str | Path) -> Manifest:
    """Read the *actual* pipeline v0.4 review_queue.json contract."""
    path = Path(path)
    data = _object(_load_json(path), "review manifest")
    if data.get("schema_version") != "0.4":
        raise ClipLedgerError("Unsupported review_queue schema_version (expected '0.4')")
    job_id = _required_string(data.get("job_id"), "job_id")
    source = _required_string(data.get("source"), "source")
    duration = _integer(data.get("duration_ms"), "duration_ms", 1)
    raw = data.get("clips")
    if not isinstance(raw, list) or len(raw) > 10:
        raise ClipLedgerError("clips must be a list of at most 10 selected candidates")
    candidate_count = _integer(data.get("candidate_count"), "candidate_count")
    count = _integer(data.get("selected_for_review"), "selected_for_review")
    if count != len(raw) or candidate_count < len(raw):
        raise ClipLedgerError("Selected, clip list, and candidate counts disagree")

    clips: list[Clip] = []
    occurrences: dict[tuple[int, int, int], int] = {}
    for rank, item in enumerate(raw, 1):
        item = _object(item, f"clips[{rank}]")
        candidate = _object(item.get("candidate"), f"clips[{rank}].candidate")
        a = _integer(candidate.get("start_ms"), f"clips[{rank}].start_ms")
        b = _integer(candidate.get("end_ms"), f"clips[{rank}].end_ms")
        anchor = _integer(candidate.get("anchor_ms"), f"clips[{rank}].anchor_ms")
        if not a < b <= duration or not a <= anchor <= b:
            raise ClipLedgerError(f"clips[{rank}] has invalid source-relative timestamps")
        preview = item.get("preview")
        if preview is not None and (not isinstance(preview, str) or not preview.strip()):
            raise ClipLedgerError(f"clips[{rank}].preview must be a path string or null")
        key = (a, b, anchor)
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        # Independent of preview filename, ranking score and category; only
        # identical spans within the same job need a collision counter.
        payload = json.dumps((job_id, source, a, b, anchor, occurrence),
                             separators=(",", ":"), ensure_ascii=False)
        clip_id = sha256(payload.encode("utf-8")).hexdigest()[:24]
        clips.append(Clip(clip_id, f"selected_{rank:03d}", a, b, anchor, preview))
    return Manifest(path, job_id, source, duration, candidate_count, tuple(clips))


def _empty_annotation(clip: Clip) -> dict[str, Any]:
    row = clip.identity()
    row.update({
        "reviewer_id": "", "decision": None,
        "hook_quality": None, "context_completeness": None,
        "payoff_completeness": None, "audio_correct": None,
        "visual_correct": None, "subtitle_readable": None,
        "duplicate_content": None, "failure_category": None, "notes": "",
    })
    return row


def _template(manifest: Manifest, source_id: str | None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "source_job_id": manifest.job_id,
        "source_path": manifest.source,
        # Default is the *exact* source string from the manifest, not
        # platform-dependent normalization of a Windows/Unix pathname.
        "source_id": _required_string(
            manifest.source if source_id is None else source_id, "source_id"),
        "clips": [_empty_annotation(clip) for clip in manifest.clips],
    }


def make_template(
    manifest: Manifest, output: str | Path, *, source_id: str | None = None,
) -> bool:
    """Create a JSON review template once. Return False if safely reusable.

    Never overwrite existing human edits, even with repeated invocations.
    A pre-existing but incompatible/invalid review document raises an error.
    """
    output = Path(output)
    expected = _template(manifest, source_id)
    if output.exists():
        actual = read_annotations(manifest, output)
        if actual["source_id"] != expected["source_id"]:
            raise ClipLedgerError("Existing review uses a different source_id; not overwritten")
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents silent clobbering in two concurrent processes.
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(expected, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return True


def read_annotations(
    manifest: Manifest, path: str | Path, *, require_complete: bool = False,
) -> dict[str, Any]:
    """Validate immutable identities and human-authored values; no label repair."""
    obj = _object(_load_json(Path(path)), "annotation file")
    if set(obj) != ROOT_FIELDS:
        raise ClipLedgerError("Annotation root keys differ from ClipLedger schema")
    if obj["schema_version"] != SCHEMA:
        raise ClipLedgerError("Unsupported ClipLedger annotation schema")
    if obj["source_job_id"] != manifest.job_id or obj["source_path"] != manifest.source:
        raise ClipLedgerError("Annotation source/job does not match the review manifest")
    _required_string(obj["source_id"], "source_id")
    rows = obj["clips"]
    if not isinstance(rows, list) or len(rows) != len(manifest.clips):
        raise ClipLedgerError("Annotation row count does not match selected clips")

    expected = {clip.clip_id: clip for clip in manifest.clips}
    seen: set[str] = set()
    for index, row in enumerate(rows):
        row = _object(row, f"annotations.clips[{index}]")
        if set(row) != ANNOTATION_FIELDS:
            raise ClipLedgerError(f"annotations.clips[{index}] has missing/unknown keys")
        clip_id = row["clip_id"]
        if not isinstance(clip_id, str) or clip_id not in expected or clip_id in seen:
            raise ClipLedgerError(f"Unknown or repeated clip_id at row {index}")
        seen.add(clip_id)
        clip = expected[clip_id]
        for field, value in clip.identity().items():
            if row[field] != value or type(row[field]) is not type(value):
                raise ClipLedgerError(f"Immutable {field} differs from manifest for {clip_id}")

        decision = row["decision"]
        if decision is not None and decision not in DECISIONS:
            raise ClipLedgerError(f"{clip_id}: decision must be good/bad/uncertain/null")
        reviewer_id = row["reviewer_id"]
        if not isinstance(reviewer_id, str):
            raise ClipLedgerError(f"{clip_id}: reviewer_id must be text")
        if decision is not None and not reviewer_id.strip():
            raise ClipLedgerError(f"{clip_id}: reviewed decision requires reviewer_id")
        for field in QUALITY:
            rating = row[field]
            if rating is not None and (type(rating) is not int or not 1 <= rating <= 5):
                raise ClipLedgerError(f"{clip_id}: {field} must be 1..5 or null")
        for field in TECH_FIELDS:
            if row[field] is not None and row[field] not in TECH:
                raise ClipLedgerError(f"{clip_id}: {field} must be yes/no/n/a/null")
        if row["duplicate_content"] is not None and row["duplicate_content"] not in DUPLICATE:
            raise ClipLedgerError(f"{clip_id}: duplicate_content must be yes/no/uncertain/null")
        category = row["failure_category"]
        if category is not None and category not in FAILURES:
            raise ClipLedgerError(f"{clip_id}: invalid failure_category")
        if decision == "bad" and category is None:
            raise ClipLedgerError(f"{clip_id}: bad decision requires a failure_category")
        if not isinstance(row["notes"], str):
            raise ClipLedgerError(f"{clip_id}: notes must be text")

        if require_complete:
            if decision is None:
                raise ClipLedgerError(f"{clip_id}: decision is still unreviewed")
            missing = [field for field in (*QUALITY, *TECH_FIELDS, "duplicate_content")
                       if row[field] is None]
            if missing:
                raise ClipLedgerError(f"{clip_id}: incomplete fields: {', '.join(missing)}")
    return obj


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _preview_status(manifest: Manifest, clip: Clip) -> str:
    if clip.preview_path is None:
        return "not_rendered"
    path = Path(clip.preview_path)
    # A Windows-absolute path on Linux is not relative to the manifest folder.
    if not path.is_absolute() and not PureWindowsPath(clip.preview_path).is_absolute():
        path = manifest.path.parent / path
    return "available" if path.is_file() else "missing"


def _overlap(a: Clip, b: Clip) -> tuple[float, float, int]:
    common = max(0, min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms))
    shorter = min(a.end_ms - a.start_ms, b.end_ms - b.start_ms)
    union = max(a.end_ms, b.end_ms) - min(a.start_ms, b.start_ms)
    return common / shorter, common / union, abs(a.anchor_ms - b.anchor_ms)


def _same_moment_hint(a: Clip, b: Clip) -> bool:
    short_fraction, iou, anchor_gap = _overlap(a, b)
    # A conservative, time-only *possible* same-event heuristic. Not
    # speaker identification, semantics or verified duplicates.
    return short_fraction >= 0.6 and (anchor_gap <= 12_000 or iou >= 0.4)


def evaluate(manifest: Manifest, annotations: dict[str, Any]) -> dict[str, Any]:
    """Rates use human-reviewed denominators; unknown rates return null."""
    by_id = {row["clip_id"]: row for row in annotations["clips"]}
    rows = [by_id[clip.clip_id] for clip in manifest.clips]
    selected = len(rows)
    reviewed = [row for row in rows if row["decision"] is not None]
    good = sum(row["decision"] == "good" for row in reviewed)
    bad = sum(row["decision"] == "bad" for row in reviewed)
    uncertain = sum(row["decision"] == "uncertain" for row in reviewed)
    judged = good + bad

    top = rows[:3]
    top_judged = sum(row["decision"] in ("good", "bad") for row in top)
    top_good = sum(row["decision"] == "good" for row in top)
    slots = len(top)
    technical = {}
    for field in TECH_FIELDS:
        yes = sum(row[field] == "yes" for row in reviewed)
        no = sum(row[field] == "no" for row in reviewed)
        na = sum(row[field] == "n/a" for row in reviewed)
        technical[field] = {
            "correct": yes, "incorrect": no, "not_applicable": na,
            "unrated_reviewed": len(reviewed) - yes - no - na,
            "rate": _ratio(yes, yes + no),
            "denominator": yes + no,
        }
    quality = {}
    for field in QUALITY:
        values = [row[field] for row in reviewed if row[field] is not None]
        quality[field] = {
            "mean_1_to_5": round(sum(values) / len(values), 3) if values else None,
            "rated": len(values),
        }

    failures = {name: 0 for name in sorted(FAILURES)}
    for row in reviewed:
        if row["decision"] == "bad":
            failures[row["failure_category"]] += 1

    duplicate_yes = sum(row["duplicate_content"] == "yes" for row in reviewed)
    duplicate_no = sum(row["duplicate_content"] == "no" for row in reviewed)
    potential_pairs = [
        (i, j)
        for i in range(selected)
        for j in range(i + 1, selected)
        if _same_moment_hint(manifest.clips[i], manifest.clips[j])
    ]
    potential_members = {i for pair in potential_pairs for i in pair}
    preview_counts = {key: 0 for key in ("available", "missing", "not_rendered")}
    for clip in manifest.clips:
        preview_counts[_preview_status(manifest, clip)] += 1

    return {
        "schema_version": "clipledger-report/1",
        "source_job_id": manifest.job_id,
        "source_id": annotations["source_id"],
        "source_path": manifest.source,
        "duration_ms": manifest.duration_ms,
        "source_candidate_count": manifest.candidate_count,
        "selected_count": selected,
        "review_coverage": {
            "reviewed": len(reviewed), "unreviewed": selected - len(reviewed),
            "uncertain": uncertain, "determinate": judged,
            "reviewed_rate": _ratio(len(reviewed), selected),
            "determinate_rate": _ratio(judged, selected),
        },
        # Full P@3 is only known when every available top-3 slot has a
        # determinate good/bad decision. Two selected => two-slot denominator.
        "precision_at_3": {
            "value": _ratio(top_good, slots) if slots and top_judged == slots else None,
            "good": top_good, "determinate": top_judged, "slots": slots,
            "coverage": _ratio(top_judged, slots),
        },
        "interesting_rate": {
            "value": _ratio(good, judged), "good": good, "bad": bad,
            "denominator": judged,
        },
        "technical_correctness": technical,
        "quality_ratings": quality,
        "failure_categories": {
            "counts_among_bad": {k: v for k, v in failures.items() if v},
            "denominator": bad,
        },
        "duplicates": {
            "reviewer_flagged": duplicate_yes,
            "reviewer_nonduplicate": duplicate_no,
            "reviewer_rate": _ratio(duplicate_yes, duplicate_yes + duplicate_no),
            "reviewer_denominator": duplicate_yes + duplicate_no,
            "potential_overlap_pairs": len(potential_pairs),
            "potential_overlap_clip_count": len(potential_members),
            "potential_overlap_clip_rate": _ratio(len(potential_members), selected),
        },
        "previews": preview_counts,
        "notice": (
            "Selected-clips-only human evaluation. Uncertain and missing decisions "
            "are excluded from good/bad denominators. Potential overlap is a timing "
            "heuristic, not confirmed duplication. No recall or virality inference."
        ),
    }


def _match_clips(old: Manifest, new: Manifest) -> list[tuple[int, int, float]]:
    """Optimal one-to-one pairing over a small, time-overlap-only graph."""
    scores: dict[tuple[int, int], float] = {}
    for i, a in enumerate(old.clips):
        for j, b in enumerate(new.clips):
            if _same_moment_hint(a, b):
                short, iou, gap = _overlap(a, b)
                scores[i, j] = iou * 100 + short * 10 + max(0, 12_000 - gap) / 12_000

    @lru_cache(maxsize=None)
    def best(i: int, used: int) -> tuple[float, tuple[tuple[int, int], ...]]:
        if i == len(old.clips):
            return 0.0, ()
        current = best(i + 1, used)
        for j in range(len(new.clips)):
            if (i, j) not in scores or used & (1 << j):
                continue
            sub_score, sub_pairs = best(i + 1, used | (1 << j))
            offer = (scores[i, j] + sub_score, ((i, j),) + sub_pairs)
            if offer[0] > current[0] + 1e-9:
                current = offer
            elif abs(offer[0] - current[0]) <= 1e-9 and offer[1] < current[1]:
                current = offer
        return current

    _, matches = best(0, 0)
    return [(i, j, round(scores[i, j], 6)) for i, j in matches]


def compare(
    old: Manifest, old_annotations: dict[str, Any],
    new: Manifest, new_annotations: dict[str, Any],
) -> dict[str, Any]:
    """Compare human-reviewed selected candidates on one aligned source clock.

    A matching source_id is an assertion by the operator, not a cryptographic
    verification of the source recording. Original labels are NEVER copied.
    """
    old_id, new_id = old_annotations["source_id"], new_annotations["source_id"]
    if old_id != new_id:
        raise ClipLedgerError(
            "Different source_id values; compare only the same recording. "
            "Regenerate templates with an explicitly shared --source-id if paths differ."
        )
    if abs(old.duration_ms - new.duration_ms) > 1_000:
        raise ClipLedgerError("Source durations differ by >1s; clocks may not align")
    old_report = evaluate(old, old_annotations)
    new_report = evaluate(new, new_annotations)
    old_labels = {row["clip_id"]: row["decision"] for row in old_annotations["clips"]}
    new_labels = {row["clip_id"]: row["decision"] for row in new_annotations["clips"]}
    pairs = _match_clips(old, new)
    matched = []
    transitions: dict[str, int] = {}
    for i, j, score in pairs:
        a, b = old.clips[i], new.clips[j]
        before = old_labels[a.clip_id]
        after = new_labels[b.clip_id]
        if before in ("good", "bad") and after in ("good", "bad"):
            key = f"{before}_to_{after}"
            transitions[key] = transitions.get(key, 0) + 1
        short, iou, gap = _overlap(a, b)
        matched.append({
            "old_clip_id": a.clip_id, "new_clip_id": b.clip_id,
            "old_candidate_id": a.candidate_id, "new_candidate_id": b.candidate_id,
            "old_span_ms": [a.start_ms, a.end_ms],
            "new_span_ms": [b.start_ms, b.end_ms],
            "old_decision": before, "new_decision": after,
            "overlap_fraction_of_shorter": round(short, 6),
            "intersection_over_union": round(iou, 6),
            "anchor_distance_ms": gap, "match_score": score,
        })
    old_used = {i for i, _, _ in pairs}
    new_used = {j for _, j, _ in pairs}

    def delta(field: str) -> float | None:
        previous = old_report[field]["value"]
        current = new_report[field]["value"]
        return round(current - previous, 6) if previous is not None and current is not None else None

    return {
        "schema_version": "clipledger-comparison/1",
        "source_id": old_id,
        "old_job_id": old.job_id,
        "new_job_id": new.job_id,
        "clock_warning": (
            "Source_id and duration agreement do not prove identical media or clock "
            "alignment; matched moments are conservative timestamp hints, not semantics."
        ),
        "source_paths_differ": old.source != new.source,
        "old": old_report, "new": new_report,
        "metric_deltas": {
            "precision_at_3": delta("precision_at_3"),
            "interesting_rate": delta("interesting_rate"),
        },
        "matched": matched,
        "transitions_determinate_matched": transitions,
        "old_only": [clip.clip_id for i, clip in enumerate(old.clips) if i not in old_used],
        "new_only": [clip.clip_id for j, clip in enumerate(new.clips) if j not in new_used],
        "interpretation": (
            "No causal improvement claim: different selected moments, incomplete human "
            "labels, and imperfect time-only matching can confound the comparison."
        ),
    }


def write_json(
    payload: dict[str, Any], output: str | Path | None,
    *, protected: tuple[str | Path, ...] = (),
) -> str:
    """Format report and optionally replace a report file, never an input label."""
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        return text
    path = Path(output)
    if any(path.resolve() == Path(p).resolve() for p in protected):
        raise ClipLedgerError("Refusing to overwrite a manifest or human annotation file")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=".clipledger-", suffix=".tmp", delete=False
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return text
