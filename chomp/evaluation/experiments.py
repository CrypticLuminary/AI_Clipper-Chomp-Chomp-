"""Compare reproducible A/B/C/D descriptors without invoking any providers."""
from __future__ import annotations

import json
from pathlib import Path

from ..artifacts import digest_file
from ..editorial_selection import read_selection
from .benchmark import benchmark
from .core import ClipLedgerError

VARIANTS = {"A": "heuristic_production", "B": "semantic_production", "C": "director_first", "D": "integrated_director"}


def compare_experiments(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or config.get("schema_version") != "chompbench-experiment/1":
        raise ClipLedgerError("Expected chompbench-experiment/1")
    rows = config.get("runs")
    if not isinstance(rows, dict) or set(rows) != set(VARIANTS):
        raise ClipLedgerError("Matched experiment requires A, B, C and D")
    def local(value):
        p = Path(value)
        return p if p.is_absolute() else path.parent / p
    events = local(config["events"])
    identity = None
    results, budgets = {}, []
    for name, row in rows.items():
        if not isinstance(row, dict) or row.get("workflow") != VARIANTS[name]:
            raise ClipLedgerError("Experiment variant/workflow mismatch")
        if not row.get("rights_reference") or not row.get("protocol_id"):
            raise ClipLedgerError("Every run requires the same explicit rights and evaluation protocol")
        if row.get("annotations_sha256") != digest_file(events):
            raise ClipLedgerError("Experiment annotation fingerprint mismatch")
        ledger_path = local(row["candidates"])
        ledger = read_selection(json.loads(ledger_path.read_text(encoding="utf-8-sig")))
        director = ledger.get("director_report")
        if director and director.get("campaign", {}).get("rights_ref") != row["rights_reference"]:
            raise ClipLedgerError("Experiment rights do not match the Director campaign")
        if row.get("manifest"):
            manifest = json.loads(local(row["manifest"]).read_text(encoding="utf-8-sig"))
            if manifest.get("rights_ref") != row["rights_reference"]:
                raise ClipLedgerError("Experiment rights do not match the production manifest")
        key = (ledger["source_id"], ledger["duration_ms"], row["rights_reference"], row["protocol_id"])
        if identity is not None and key != identity:
            raise ClipLedgerError("Experiments must use the same source, rights and protocol")
        identity = key
        budget = row.get("budget")
        if not isinstance(budget, dict) or any(field not in budget for field in (
            "provider_call_limit", "candidate_limit", "inspected_footage_ms", "available_modalities")):
            raise ClipLedgerError("Report actual experiment budgets and modalities, including nulls")
        for field in ("provider_call_limit", "candidate_limit", "inspected_footage_ms"):
            value = budget[field]
            if value is not None and (type(value) is not int or value < 0):
                raise ClipLedgerError("Budget counts must be nonnegative integers or null")
        modalities = budget["available_modalities"]
        if modalities is not None and (not isinstance(modalities, list) or
                any(not isinstance(item, str) or not item.strip() for item in modalities) or len(modalities) != len(set(modalities))):
            raise ClipLedgerError("Report available modalities as a unique string list or null")
        budgets.append(budget)
        results[name] = {"workflow": VARIANTS[name], "budget": budget,
                         "metrics": benchmark(events, ledger_path,
                            manifest_path=local(row["manifest"]) if row.get("manifest") else None,
                            reviews=tuple(local(p) for p in row.get("reviews", [])))}
    known = all(all(value is not None for value in budget.values()) for budget in budgets)
    equal = all(budget == budgets[0] for budget in budgets[1:])
    matched = equal if known or not equal else None
    return {"schema_version": "chompbench-comparison/1", "benchmark_name": "ChompBench-1", "runs": results,
            "budgets_matched": matched, "budget_differences": None if matched else {k: rows[k]["budget"] for k in rows},
            "quality_conclusion": None, "quality_note": "Independent human judgments are required for quality claims."}
