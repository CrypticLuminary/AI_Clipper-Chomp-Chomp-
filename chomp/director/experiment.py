"""Matched text-only and sampled-frame comparisons on frozen evidence."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from time import perf_counter

from ..artifacts import artifact_key, artifact_lock, digest_file, json_text, write_json, write_text
from .contract import CONTROLLER_FIELDS, read_json, schema, validate_plan
from .evidence import read_verified_bundle
from .provider import PROMPT, PROMPT_SHA256, PROMPT_VERSION, evidence_view


def _mode_run(bundle: dict, images: list[bytes], root: Path, mode: str, provider) -> dict:
    payload = evidence_view(bundle, mode)
    prompt = PROMPT + "\nEVIDENCE_JSON\n" + json_text(payload)
    request = {
        "evidence_hash": bundle["evidence_hash"], "prompt_version": PROMPT_VERSION,
        "prompt_sha256": PROMPT_SHA256, "request_text_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
        "plan_schema_sha256": artifact_key(schema("editorial_plan")),
        "mode": mode, "image_ids": [row["id"] for row in payload["visual_samples"]],
        "provider": provider.name, "model": provider.model, "timeout_seconds": provider.timeout,
    }
    folder = root / mode
    status_file = folder / "status.json"
    if folder.exists():
        status = read_json(status_file)
        if any(status.get(key) != value for key, value in request.items()):
            raise ValueError(f"Choose a fresh output directory for a changed {mode} request")
        if status["status"] == "running":
            raise ValueError(f"The {mode} request was interrupted. Choose a new output directory to retry explicitly")
        if status["status"] == "validated":
            plan_path = folder / "plan.json"
            if digest_file(plan_path) != status["plan_sha256"]:
                raise ValueError(f"The saved {mode} plan changed after validation")
            validate_plan(read_json(plan_path), bundle, expected_mode=mode)
        return status

    folder.mkdir(parents=True)
    write_json(folder / "request.json", payload)
    write_json(status_file, {**request, "status": "running"})
    started = perf_counter()
    result = provider.complete(prompt, images if mode == "frames" else [])
    write_text(folder / "stdout.jsonl", result.stdout)
    write_text(folder / "stderr.txt", result.stderr)
    status = {**request, "status": result.status, "cli_version": result.cli_version,
              "elapsed_seconds": round(perf_counter() - started, 3), "usage": result.usage,
              "exit_code": result.exit_code, "validation_error": None}
    if result.decision is not None:
        write_json(folder / "model_response.json", result.decision)
        try:
            if any(key in result.decision for key in CONTROLLER_FIELDS):
                raise ValueError("Model output cannot set controlled plan fields")
            plan = {
                **result.decision, "review_status": "pending", "publish_status": "disabled",
                "provenance": {
                    "bundle_sha256": bundle["evidence_hash"], "source_sha256": bundle["source"]["sha256"],
                    "mode": mode, "provider": provider.name, "model": provider.model,
                    "cli_version": result.cli_version, "prompt_sha256": PROMPT_SHA256,
                },
            }
            validate_plan(plan, bundle, expected_mode=mode)
        except (ValueError, TypeError) as exc:
            status.update(status="validation_failed", validation_error=str(exc))
        else:
            plan_path = folder / "plan.json"
            write_json(plan_path, plan)
            status.update(status="validated", plan_sha256=digest_file(plan_path), decision=plan["decision"])
    write_json(status_file, status)
    return status


def _review_packet(out: Path, bundle: dict, results: list[dict]) -> dict:
    """Use neutral labels and keep the mode mapping outside the review packet."""
    if any(row["status"] != "validated" for row in results):
        return {}
    review = out / "blind-review"
    modes = ["text", "frames"]
    if int(bundle["evidence_hash"][-2:], 16) % 2:
        modes.reverse()
    mapping = dict(zip(("A", "B"), modes))
    for label, mode in mapping.items():
        plan = read_json(out / mode / "plan.json")
        brief = {key: value for key, value in plan.items() if key not in CONTROLLER_FIELDS}
        write_json(review / f"{label}.json", brief)
    scores = review / "ratings.template.json"
    if not scores.exists():
        write_json(scores, {
            "candidate_id": bundle["candidate"]["id"], "reviewer": None, "played_source": None,
            "source_window_ms": bundle["context"], "scale": "0 = invalid, 2 = needs rework, 4 = strong",
            "scores": {label: {"selection": None, "story": None, "grounding": None,
                               "composition": None, "title": None, "notes": None} for label in mapping},
            "preferred": None,
        })
    return mapping


def compare(bundle_path: Path, source: Path, out: Path, provider) -> dict:
    bundle, images = read_verified_bundle(bundle_path, source)
    if not images:
        raise ValueError("The comparison needs at least one decoded source frame")
    out = out.expanduser().resolve()
    results = []
    with artifact_lock(out / ".lock", timeout=10):
        for mode in ("text", "frames"):
            result = _mode_run(bundle, images, out, mode, provider)
            results.append(result)
            if result["status"] in {"quota_blocked", "provider_unavailable", "model_unavailable",
                                    "unexpected_tool_use"}:
                break
        mapping = _review_packet(out, bundle, results) if len(results) == 2 else {}
        report = {
            "schema_version": "chomp-editorial-comparison/1", "bundle_sha256": bundle["evidence_hash"],
            "candidate_id": bundle["candidate"]["id"], "source_sha256": bundle["source"]["sha256"],
            "split": bundle["source"]["split"], "results": results, "blind_review_mapping": mapping,
            "review_status": "pending", "publish_status": "disabled",
        }
        write_json(out / "comparison.json", report)
    return report
