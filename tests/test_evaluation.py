"""EVAL-07 synthetic fixtures: no real footage, internet, or model calls."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from chomp.evaluation import (
    ClipLedgerError, compare, evaluate, make_template, read_annotations,
    read_manifest, write_json,
)


def manifest_file(
    folder: Path, name="run-a", spans=((10_000, 20_000, 15_000),),
    source="C:\\VOD source files\\same capture.mp4", duration=120_000,
    preview_state="available", candidate_count=None,
):
    folder.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, (start, end, anchor) in enumerate(spans, 1):
        preview = folder / f"candidate {i:02d} with spaces.mp4"
        if preview_state == "available":
            preview.touch()
        clips.append({
            "candidate": {
                "start_ms": start, "end_ms": end, "anchor_ms": anchor,
                "score": 93.5, "scores": {"hook_strength": 5},
                "ranking_method": "heuristic_provisional",
            },
            "preview": str(preview) if preview_state != "not_rendered" else None,
            "edit_spec": str(folder / f"candidate {i:02d}.edit.json"),
            "review_status": "pending",
        })
    payload = {
        "schema_version": "0.4", "job_id": name, "source": source,
        "duration_ms": duration,
        "candidate_count": len(clips) if candidate_count is None else candidate_count,
        "selected_for_review": len(clips), "clips": clips,
    }
    path = folder / "review_queue.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def reviews_file(manifest_path: Path, *, source_id=None):
    manifest = read_manifest(manifest_path)
    path = manifest_path.parent / "human reviews with spaces.json"
    assert make_template(manifest, path, source_id=source_id)
    return manifest, path


def rate(path: Path, decisions, *, duplicates=None, technical=None):
    data = json.loads(path.read_text(encoding="utf-8"))
    for i, decision in enumerate(decisions):
        if decision is None:
            continue
        row = data["clips"][i]
        row.update({
            "reviewer_id": "human-reviewer-1", "decision": decision,
            "hook_quality": 4, "context_completeness": 3,
            "payoff_completeness": 5,
            "audio_correct": "yes", "visual_correct": "yes",
            "subtitle_readable": "n/a",
            "duplicate_content": "no",
            "failure_category": "weak_hook" if decision == "bad" else None,
            "notes": "synthetic annotation - not a real review",
        })
        if duplicates is not None:
            row["duplicate_content"] = duplicates[i]
        if technical is not None:
            row["audio_correct"] = technical[i]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def test_manifest_contract_and_stable_clip_ids_without_filename_identity(tmp_path):
    path = manifest_file(tmp_path, spans=((1000, 4000, 2500), (8000, 9000, 8400)))
    first = read_manifest(path)
    payload = json.loads(path.read_text())
    assert first.job_id == "run-a"
    assert first.clips[0].candidate_id == "selected_001"
    assert first.clips[0].clip_id == read_manifest(path).clips[0].clip_id
    assert first.clips[0].clip_id != first.clips[1].clip_id
    payload["clips"][0]["preview"] = str(tmp_path / "totally different filename.mp4")
    payload["clips"][0]["candidate"]["score"] = 0.0
    path.write_text(json.dumps(payload))
    assert read_manifest(path).clips[0].clip_id == first.clips[0].clip_id


@pytest.mark.parametrize("mutation", [
    lambda d: d.pop("clips"),
    lambda d: d.update(selected_for_review=2),
    lambda d: d.update(candidate_count=0),
    lambda d: d["clips"][0]["candidate"].update(start_ms=-1),
    lambda d: d["clips"][0]["candidate"].update(end_ms=120001),
    lambda d: d["clips"][0]["candidate"].update(anchor_ms=0),
    lambda d: d["clips"][0]["candidate"].update(start_ms=1.5),
    lambda d: d.update(duration_ms="120000"),
    lambda d: d.update(schema_version="0.5"),
    lambda d: d["clips"][0].update(preview=42),
])
def test_malformed_manifest_rejected(tmp_path, mutation):
    path = manifest_file(tmp_path)
    data = json.loads(path.read_text())
    mutation(data)
    path.write_text(json.dumps(data))
    with pytest.raises(ClipLedgerError):
        read_manifest(path)


def test_invalid_json_manifest_rejected(tmp_path):
    path = tmp_path / "review_queue.json"
    path.write_text("{missing")
    with pytest.raises(ClipLedgerError, match="Invalid JSON"):
        read_manifest(path)


def test_zero_candidates_have_null_rates_and_valid_empty_ledger(tmp_path):
    manifest_path = manifest_file(tmp_path, spans=(), candidate_count=0)
    manifest, path = reviews_file(manifest_path)
    reviews = read_annotations(manifest, path, require_complete=True)
    result = evaluate(manifest, reviews)
    assert result["selected_count"] == 0
    assert result["review_coverage"]["reviewed"] == 0
    assert result["review_coverage"]["reviewed_rate"] is None
    assert result["precision_at_3"]["value"] is None
    assert result["interesting_rate"]["value"] is None
    assert result["duplicates"]["potential_overlap_clip_rate"] is None
    assert result["technical_correctness"]["audio_correct"]["rate"] is None


def test_two_clips_precision_uses_two_available_slots(tmp_path):
    mp = manifest_file(tmp_path, spans=((1_000, 9_000, 4_000),
                                       (30_000, 40_000, 35_000)))
    m, path = reviews_file(mp)
    rate(path, ("good", "bad"))
    report = evaluate(m, read_annotations(m, path, require_complete=True))
    assert report["precision_at_3"] == {
        "value": 0.5, "good": 1, "determinate": 2, "slots": 2, "coverage": 1.0
    }
    assert report["interesting_rate"]["value"] == 0.5
    assert report["review_coverage"]["reviewed_rate"] == 1


def test_three_clips_precision_and_technical_denominators(tmp_path):
    mp = manifest_file(tmp_path, spans=((1_000, 9_000, 4_000),
                                       (30_000, 40_000, 35_000),
                                       (60_000, 72_000, 66_000)))
    m, path = reviews_file(mp)
    rate(path, ("good", "bad", "good"), technical=("yes", "no", "n/a"))
    r = evaluate(m, read_annotations(m, path, require_complete=True))
    assert r["precision_at_3"]["value"] == pytest.approx(2 / 3, abs=1e-6)
    assert r["interesting_rate"]["value"] == pytest.approx(2 / 3, abs=1e-6)
    assert r["technical_correctness"]["audio_correct"] == {
        "correct": 1, "incorrect": 1, "not_applicable": 1,
        "unrated_reviewed": 0, "rate": .5, "denominator": 2
    }
    assert r["technical_correctness"]["subtitle_readable"]["rate"] is None
    assert r["failure_categories"]["counts_among_bad"] == {"weak_hook": 1}
    assert r["quality_ratings"]["hook_quality"]["rated"] == 3


def test_incomplete_reviews_and_uncertain_do_not_invent_labels(tmp_path):
    mp = manifest_file(tmp_path, spans=((1_000, 9_000, 4_000),
                                       (30_000, 40_000, 35_000),
                                       (60_000, 72_000, 66_000)))
    m, path = reviews_file(mp)
    rate(path, ("good", "uncertain", None))
    payload = json.loads(path.read_text())
    payload["clips"][0]["audio_correct"] = None
    path.write_text(json.dumps(payload))
    report = evaluate(m, read_annotations(m, path))
    assert report["review_coverage"]["reviewed"] == 2
    assert report["review_coverage"]["uncertain"] == 1
    assert report["review_coverage"]["determinate"] == 1
    assert report["interesting_rate"]["value"] == 1
    assert report["precision_at_3"]["value"] is None
    assert report["precision_at_3"]["coverage"] == pytest.approx(1 / 3, abs=1e-6)
    assert report["technical_correctness"]["audio_correct"]["unrated_reviewed"] == 1
    with pytest.raises(ClipLedgerError, match="incomplete fields"):
        read_annotations(m, path, require_complete=True)


def test_unreviewed_technical_fields_do_not_count(tmp_path):
    mp = manifest_file(tmp_path)
    m, path = reviews_file(mp)
    data = json.loads(path.read_text())
    data["clips"][0]["audio_correct"] = "no"  # unfinished draft, no decision
    path.write_text(json.dumps(data))
    r = evaluate(m, read_annotations(m, path))
    assert r["technical_correctness"]["audio_correct"]["denominator"] == 0
    assert r["review_coverage"]["reviewed"] == 0


def test_missing_preview_and_unrendered_are_not_bad_labels(tmp_path):
    mp = manifest_file(tmp_path, preview_state="missing")
    m, path = reviews_file(mp)
    r = evaluate(m, read_annotations(m, path))
    assert r["previews"]["missing"] == 1
    assert r["interesting_rate"]["value"] is None
    other = tmp_path / "different"
    mp2 = manifest_file(other, preview_state="not_rendered")
    m2, path2 = reviews_file(mp2)
    assert evaluate(m2, read_annotations(m2, path2))["previews"]["not_rendered"] == 1


@pytest.mark.parametrize("field,value", [
    ("decision", "viral"), ("decision", []),
    ("hook_quality", True), ("hook_quality", 6),
    ("context_completeness", 1.0),
    ("audio_correct", "probably"), ("audio_correct", []),
    ("visual_correct", "pass"), ("subtitle_readable", 22),
    ("duplicate_content", "unknown"), ("duplicate_content", []),
    ("failure_category", "fake_category"), ("failure_category", []),
    ("notes", 5), ("reviewer_id", []),
])
def test_invalid_human_rating_rejected(tmp_path, field, value):
    mp = manifest_file(tmp_path)
    m, path = reviews_file(mp)
    data = json.loads(path.read_text())
    data["clips"][0][field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ClipLedgerError):
        read_annotations(m, path)


def test_decision_requires_reviewer_and_bad_requires_category(tmp_path):
    mp = manifest_file(tmp_path)
    m, path = reviews_file(mp)
    data = json.loads(path.read_text())
    data["clips"][0]["decision"] = "good"
    path.write_text(json.dumps(data))
    with pytest.raises(ClipLedgerError, match="reviewer_id"):
        read_annotations(m, path)
    data["clips"][0]["reviewer_id"] = "Alice"
    data["clips"][0]["decision"] = "bad"
    path.write_text(json.dumps(data))
    with pytest.raises(ClipLedgerError, match="failure_category"):
        read_annotations(m, path)


@pytest.mark.parametrize("field,value", [
    ("start_ms", 50), ("end_ms", 50), ("anchor_ms", 50),
    ("candidate_id", "other"), ("clip_id", "unknown"),
    ("preview_path", "other.mp4"),
])
def test_immutable_identity_cannot_be_changed(tmp_path, field, value):
    mp = manifest_file(tmp_path)
    m, path = reviews_file(mp)
    data = json.loads(path.read_text())
    data["clips"][0][field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ClipLedgerError):
        read_annotations(m, path)


def test_duplicate_annotation_rows_and_unknown_root_fields_rejected(tmp_path):
    mp = manifest_file(tmp_path, spans=((1000,2000,1500),(3000,4000,3500)))
    m, path = reviews_file(mp)
    data = json.loads(path.read_text())
    data["clips"][1] = dict(data["clips"][0])
    path.write_text(json.dumps(data))
    with pytest.raises(ClipLedgerError, match="repeated"):
        read_annotations(m, path)
    data = json.loads(path.read_text())
    data["clips"][1] = read_manifest(mp).clips[1].identity() | {
        k: None for k in ("decision", "hook_quality", "context_completeness",
                           "payoff_completeness", "audio_correct", "visual_correct",
                           "subtitle_readable", "duplicate_content", "failure_category")
    } | {"reviewer_id": "", "notes": ""}
    data["extra"] = True
    path.write_text(json.dumps(data))
    with pytest.raises(ClipLedgerError, match="root keys"):
        read_annotations(m, path)


def test_overlap_rate_and_human_duplicate_rate_are_distinct(tmp_path):
    mp = manifest_file(tmp_path, spans=((10_000, 26_000, 19_000),
                                       (12_000, 28_000, 21_000),
                                       (80_000, 91_000, 85_000)))
    m, path = reviews_file(mp)
    rate(path, ("good", "bad", "uncertain"),
         duplicates=("no", "yes", "uncertain"))
    r = evaluate(m, read_annotations(m, path))
    assert r["duplicates"]["potential_overlap_pairs"] == 1
    assert r["duplicates"]["potential_overlap_clip_count"] == 2
    assert r["duplicates"]["potential_overlap_clip_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert r["duplicates"]["reviewer_rate"] == 0.5
    assert r["duplicates"]["reviewer_denominator"] == 2


def test_same_span_multiple_rows_have_distinct_stable_ids(tmp_path):
    mp = manifest_file(tmp_path, spans=((1000,9000,5000),(1000,9000,5000)))
    m = read_manifest(mp)
    assert m.clips[0].clip_id != m.clips[1].clip_id
    assert [clip.clip_id for clip in read_manifest(mp).clips] == [
        clip.clip_id for clip in m.clips
    ]


def test_repeated_template_preserves_human_labels_exactly(tmp_path):
    mp = manifest_file(tmp_path)
    m, path = reviews_file(mp)
    rate(path, ("good",))
    before = path.read_bytes()
    assert make_template(m, path) is False
    assert path.read_bytes() == before
    assert read_annotations(m, path)["clips"][0]["decision"] == "good"
    with pytest.raises(ClipLedgerError, match="different source_id"):
        make_template(m, path, source_id="a different recording")


def test_modified_manifest_does_not_silently_reuse_labels(tmp_path):
    mp = manifest_file(tmp_path)
    old, path = reviews_file(mp)
    rate(path, ("good",))
    data = json.loads(mp.read_text())
    data["clips"][0]["candidate"]["start_ms"] = 11_000
    mp.write_text(json.dumps(data))
    with pytest.raises(ClipLedgerError):
        make_template(read_manifest(mp), path)
    assert read_annotations(old, path)["clips"][0]["decision"] == "good"


def test_same_event_different_times_compares_without_filename_matching(tmp_path):
    source = "D:\\stream archives\\source 1.mkv"
    old_mp = manifest_file(tmp_path / "old", name="old",
                           spans=((10_000,25_000,19_000),(70_000,84_000,77_000)),
                           source=source)
    new_mp = manifest_file(tmp_path / "new", name="new",
                           spans=((13_000,29_000,20_000),(95_000,110_000,100_000)),
                           source=source)
    old, old_path = reviews_file(old_mp)
    new, new_path = reviews_file(new_mp)
    rate(old_path, ("bad", "good"))
    rate(new_path, ("good", "good"))
    result = compare(old, read_annotations(old, old_path),
                     new, read_annotations(new, new_path))
    assert result["metric_deltas"]["interesting_rate"] == 0.5
    assert result["transitions_determinate_matched"] == {"bad_to_good": 1}
    assert len(result["matched"]) == 1
    assert len(result["old_only"]) == 1 and len(result["new_only"]) == 1
    assert result["matched"][0]["old_candidate_id"] == "selected_001"
    assert result["matched"][0]["anchor_distance_ms"] == 1_000
    assert result["matched"][0]["old_clip_id"] != result["matched"][0]["new_clip_id"]


def test_cross_platform_source_paths_require_explicit_equal_source_id(tmp_path):
    left = manifest_file(tmp_path / "left", name="old", source="C:\\raw vod\\match 1.mp4")
    right = manifest_file(tmp_path / "right", name="new", source="/mnt/streams/match 1.mp4")
    old, old_path = reviews_file(left)
    new, new_path = reviews_file(right)
    with pytest.raises(ClipLedgerError, match="Different source_id"):
        compare(old, read_annotations(old, old_path),
                new, read_annotations(new, new_path))
    old_path.unlink()
    new_path.unlink()
    make_template(old, old_path, source_id="capture-sha256-externally-verified")
    make_template(new, new_path, source_id="capture-sha256-externally-verified")
    r = compare(old, read_annotations(old, old_path),
                new, read_annotations(new, new_path))
    assert r["source_paths_differ"] is True
    assert len(r["matched"]) == 1


def test_different_source_durations_rejected(tmp_path):
    a = manifest_file(tmp_path / "a", name="old", duration=120_000)
    b = manifest_file(tmp_path / "b", name="new", duration=130_000)
    old, op = reviews_file(a, source_id="same")
    new, np = reviews_file(b, source_id="same")
    with pytest.raises(ClipLedgerError, match="durations"):
        compare(old, read_annotations(old, op), new, read_annotations(new, np))


def test_ambiguous_competing_matches_are_one_to_one(tmp_path):
    a = manifest_file(tmp_path / "old", name="old", spans=((1000,21000,10000),))
    b = manifest_file(tmp_path / "new", name="new", spans=(
        (1000,19000,10000),(1200,19500,10100)))
    old, op = reviews_file(a)
    new, np = reviews_file(b)
    r = compare(old, read_annotations(old, op), new, read_annotations(new, np))
    assert len(r["matched"]) == 1 and len(r["new_only"]) == 1


def test_missing_comparison_labels_produce_null_deltas(tmp_path):
    a = manifest_file(tmp_path / "old", name="old")
    b = manifest_file(tmp_path / "new", name="new")
    old, op = reviews_file(a)
    new, np = reviews_file(b)
    rate(op, ("good",))
    result = compare(old, read_annotations(old, op), new, read_annotations(new, np))
    assert result["metric_deltas"]["precision_at_3"] is None
    assert result["metric_deltas"]["interesting_rate"] is None
    assert len(result["matched"]) == 1


def test_reports_cannot_overwrite_source_manifest_or_human_labels(tmp_path):
    mp = manifest_file(tmp_path)
    m, path = reviews_file(mp)
    report = evaluate(m, read_annotations(m, path))
    with pytest.raises(ClipLedgerError, match="Refusing"):
        write_json(report, path, protected=(mp, path))
    with pytest.raises(ClipLedgerError, match="Refusing"):
        write_json(report, mp, protected=(mp, path))
    assert path.is_file() and read_annotations(m, path)["clips"]


def test_cli_template_validate_report_and_compare_with_spaces(tmp_path):
    a = manifest_file(tmp_path / "Windows folder with spaces" / "old", name="A")
    b = manifest_file(tmp_path / "Windows folder with spaces" / "new", name="B")
    reviews_a = a.parent / "annotated clips.json"
    reviews_b = b.parent / "annotated clips.json"
    def invoke(*args):
        result = subprocess.run(
            [sys.executable, "-m", "chomp.evaluation", *map(str, args)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout
    invoke("template", a, "--out", reviews_a)
    invoke("template", b, "--out", reviews_b)
    rate(reviews_a, ("good",))
    rate(reviews_b, ("bad",))
    invoke("validate", "--manifest", a, "--reviews", reviews_a, "--require-complete")
    report_out = a.parent / "evaluation report.json"
    invoke("report", "--manifest", a, "--reviews", reviews_a, "--out", report_out)
    assert json.loads(report_out.read_text())["precision_at_3"]["value"] == 1.0
    comparison_out = a.parent / "comparison report.json"
    invoke("compare", "--old-manifest", a, "--old-reviews", reviews_a,
           "--new-manifest", b, "--new-reviews", reviews_b, "--out", comparison_out)
    assert json.loads(comparison_out.read_text())["metric_deltas"]["interesting_rate"] == -1.0


def test_reproducible_synthetic_demo_never_overwrites(tmp_path):
    from chomp.evaluation.demo import generate
    folder = tmp_path / "demo outputs with spaces"
    created = generate(folder)
    assert Path(created["comparison"]).is_file()
    assert Path(created["old_review"]).is_file()
    assert Path(created["new_review"]).is_file()
    old = json.loads(Path(created["old_report"]).read_text(encoding="utf-8"))
    new = json.loads(Path(created["new_report"]).read_text(encoding="utf-8"))
    comparison = json.loads(Path(created["comparison"]).read_text(encoding="utf-8"))
    assert old["precision_at_3"]["value"] is None  # synthetic uncertain label
    assert old["interesting_rate"]["value"] == .5
    assert new["precision_at_3"]["value"] == pytest.approx(2/3, abs=1e-6)
    assert comparison["metric_deltas"]["interesting_rate"] == .166667
    assert comparison["transitions_determinate_matched"] == {
        "good_to_good": 1, "bad_to_good": 1,
    }
    original = Path(created["old_review"]).read_bytes()
    with pytest.raises(FileExistsError):
        generate(folder)
    assert Path(created["old_review"]).read_bytes() == original
