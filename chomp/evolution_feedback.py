"""Append-only EVOLUTION feedback and explicit, versioned local interest training."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from math import exp, isfinite, log
from pathlib import Path
from time import perf_counter

from .artifacts import artifact_key, artifact_lock, json_text, write_json
from .director.contract import read_json

REASONS = ("interest", "incomplete", "misleading", "wrong-start", "wrong-end", "wrong-speaker",
           "composition", "captions", "audio", "technical")
FEATURE_COUNT = 9
SCHEMA = "chomp-evolution-interest/1"


def _vector(features):
    if (not isinstance(features, list) or len(features) != FEATURE_COUNT
            or any(type(x) not in (int, float) or not isfinite(x) or not 0 <= x <= 1 for x in features)):
        raise ValueError("Invalid EVOLUTION interest feature vector")
    return features


def add_feedback(ledger: Path, destination: Path, args) -> dict:
    job = read_json(ledger)
    row = next((row for row in job["candidates"] if row["candidate_id"] == args.candidate), None)
    if job.get("schema_version") != "chomp-candidates/1" or row is None:
        raise ValueError("Candidate is missing from the supplied EVOLUTION ledger")
    if not args.reviewer.strip():
        raise ValueError("Enter a nonempty reviewer identifier")
    span = [args.start_ms, args.end_ms]
    if any(x is not None and not 0 <= x <= job["duration_ms"] for x in span) or (
            None not in span and span[0] >= span[1]):
        raise ValueError("Corrected boundaries must lie on the source clock")
    preferred = None
    if args.preferred:
        preferred = next((r for r in job["candidates"] if r["candidate_id"] == args.preferred), None)
        if preferred is None or args.candidate == args.preferred:
            raise ValueError("The preferred candidate must be a different candidate in this ledger")
        if args.reason != "interest":
            raise ValueError("Record preferences only for editorial interest")
    now = datetime.now(timezone.utc).isoformat()
    entry = {"origin": "human", "timestamp_utc": now, "source_id": job["source_id"], "job_id": job["job_id"],
             "reviewer": args.reviewer, "candidate_id": args.candidate, "decision": args.decision,
             "reason": args.reason, "notes": args.notes, "corrected_span_ms": span,
             "preferred_candidate_id": args.preferred,
             "preferred_features": _vector(preferred["feature_vector"]) if preferred else None,
             "features": _vector(row["feature_vector"]), "original_prediction": row["candidate"]}
    with artifact_lock(destination.with_suffix(destination.suffix + ".lock")):
        data = _feedback(destination) if destination.exists() else []
        duplicate = next((previous for previous in reversed(data)
                          if previous["reviewer"] == entry["reviewer"] and previous["job_id"] == entry["job_id"]
                          and previous["candidate_id"] == entry["candidate_id"] and previous["reason"] == entry["reason"]), None)
        if duplicate and all(duplicate.get(key) == value for key, value in entry.items() if key != "timestamp_utc"):
            return duplicate
        data.append(entry)
        write_json(destination, {"schema_version": "chomp-evolution-feedback/1", "records": data})
    return entry


def _feedback(path: Path) -> list[dict]:
    data = read_json(path)
    if data["schema_version"] != "chomp-evolution-feedback/1" or not isinstance(data["records"], list):
        raise ValueError("Expected EVOLUTION feedback JSON")
    return data["records"]


def import_clipledger(ledger: Path, queue: Path, annotations: Path, destination: Path) -> dict:
    from .evaluation.core import read_annotations, read_manifest
    from types import SimpleNamespace

    manifest = read_manifest(queue)
    queue_data = read_json(queue)
    if queue_data.get("engine") != "evolution" or Path(queue_data["candidate_ledger"]).resolve() != ledger.resolve():
        raise ValueError("Review queue and EVOLUTION candidate ledger do not match")
    result = read_annotations(manifest, annotations)
    if manifest.source_id and result["source_id"] != manifest.source_id:
        raise ValueError("Annotation source identity differs from the queue")
    imported = 0
    for feedback in result["clips"]:
        decision = feedback["decision"]
        if decision not in {"good", "bad", "uncertain"}:
            continue
        reasons = {"uninteresting": "interest", "audio": "audio", "framing": "composition",
                   "subtitles": "captions", "wrong_timing": "wrong-start"}
        reason = reasons.get(feedback["failure_category"], "incomplete")
        args = SimpleNamespace(candidate=feedback["candidate_id"], reviewer=feedback["reviewer_id"], decision=decision,
                               reason=reason, start_ms=None, end_ms=None, preferred=None,
                               notes=json_text(feedback))
        add_feedback(ledger, destination, args)
        imported += 1
    return {"imported": imported}


def _sigmoid(activation):
    return 1 / (1 + exp(-max(-40, min(40, activation))))


def interest_score(model, features):
    return _sigmoid(model["bias"] + sum(w * x for w, x in zip(model["weights"], _vector(features))))


def load_model(path: Path) -> dict:
    model = read_json(path)
    signature = {k: v for k, v in model.items() if k != "model_id"}
    if (model.get("schema_version") != SCHEMA or model.get("origin") != "human" or
            model.get("model_id") != artifact_key(signature)):
        raise ValueError("Expected an intact human-trained EVOLUTION model version")
    parameters = [model["bias"], *model["weights"]]
    if len(parameters) != FEATURE_COUNT + 1 or any(type(x) not in (int, float) or not isfinite(x) for x in parameters):
        raise ValueError("Invalid EVOLUTION interest parameters")
    return model


def train(feedback: Path, output: Path, *, synthetic: bool = False) -> dict:
    started = perf_counter()
    origin = "synthetic" if synthetic else "human"
    # Later explicit labels supersede earlier labels from the same reviewer without deleting records.
    latest = {(r["source_id"], r["candidate_id"], r["reviewer"]): r for r in _feedback(feedback)
              if r.get("origin") == origin and r.get("reason") == "interest"}
    examples = []
    for row in latest.values():
        features = _vector(row["features"])
        if row["decision"] in {"good", "bad"}:
            examples.append((row["source_id"], features, int(row["decision"] == "good"), False))
        if row.get("preferred_features"):
            preferred = _vector(row["preferred_features"])
            delta = [b - a for a, b in zip(features, preferred)]
            examples.extend([(row["source_id"], delta, 1, True),
                             (row["source_id"], [-x for x in delta], 0, True)])
    if len(examples) < 4 or {x[2] for x in examples} != {0, 1}:
        raise ValueError("Need at least four explicit interest examples with both labels or two preferences")
    sources = sorted({r[0] for r in examples})
    held_out = sources[-1] if len(sources) >= 3 else None
    training = [r for r in examples if r[0] != held_out]
    if {r[2] for r in training} != {0, 1}:
        held_out, training = None, examples
    weights = [0.0] * FEATURE_COUNT
    bias = 0.0
    for _ in range(240):
        gradient = [0.04 * w * len(training) for w in weights]
        bias_gradient = 0.0
        for _, features, expected, pair in training:
            predicted = _sigmoid((0 if pair else bias) + sum(w * x for w, x in zip(weights, features)))
            error = predicted - expected
            gradient = [g + error * x for g, x in zip(gradient, features)]
            if not pair:
                bias_gradient += error
        weights = [w - .45 * g / len(training) for w, g in zip(weights, gradient)]
        bias -= .45 * bias_gradient / len(training)
    metrics = None
    hold = [r for r in examples if r[0] == held_out]
    if hold:
        correct, local_correct, losses = 0, 0, []
        for _, features, expected, pair in hold:
            prediction = _sigmoid((0 if pair else bias) + sum(w * x for w, x in zip(weights, features)))
            local = features[0] >= (0 if pair else .58)
            correct += (prediction >= .5) == bool(expected)
            local_correct += local == bool(expected)
            losses.append(-log(max(1e-12, prediction if expected else 1 - prediction)))
        metrics = {"examples": len(hold), "accuracy": correct / len(hold),
                   "local_rule_accuracy": local_correct / len(hold), "log_loss": sum(losses) / len(losses)}
    model = {"schema_version": SCHEMA, "origin": origin, "weights": weights, "bias": bias, "feature_count": FEATURE_COUNT,
             "examples": len(training), "sources": len(sources), "source_holdout": held_out,
             "holdout_metrics": metrics, "training_seconds": round(perf_counter() - started, 6)}
    model["model_id"] = artifact_key(model)
    target = output / f"{model['model_id']}.json"
    if not target.exists():
        try:
            write_json(target, model, exclusive=True)
        except FileExistsError:
            pass
    return {"model": str(target), "size_bytes": target.stat().st_size, **model}


def command(name: str, argv: list[str]) -> int:
    if name == "hardware":
        from .evolution_hardware import hardware
        parser = argparse.ArgumentParser(prog="chomp hardware")
        parser.parse_args(argv)
        print(json_text(hardware()), end="")
        return 0
    parser = argparse.ArgumentParser(prog=f"chomp {name}")
    if name == "evolution-feedback":
        parser.add_argument("--ledger", required=True, type=Path)
        parser.add_argument("--output", required=True, type=Path)
        parser.add_argument("--candidate")
        parser.add_argument("--reviewer")
        parser.add_argument("--decision", choices=("good", "bad", "uncertain"))
        parser.add_argument("--reason", choices=REASONS, default="interest")
        parser.add_argument("--notes", default="")
        parser.add_argument("--start-ms", type=int)
        parser.add_argument("--end-ms", type=int)
        parser.add_argument("--preferred")
        parser.add_argument("--clipledger", type=Path)
        parser.add_argument("--queue", type=Path)
        args = parser.parse_args(argv)
        if bool(args.clipledger) != bool(args.queue) or (not args.clipledger and not (args.candidate and args.decision and args.reviewer)):
            parser.error("Supply a ClipLedger file and review queue, or a candidate ID and decision")
    else:
        parser.add_argument("--feedback", required=True, type=Path)
        parser.add_argument("--output", required=True, type=Path)
        parser.add_argument("--synthetic", action="store_true")
        args = parser.parse_args(argv)
    try:
        if name == "evolution-train":
            result = train(args.feedback, args.output, synthetic=args.synthetic)
        elif args.clipledger:
            result = import_clipledger(args.ledger, args.queue, args.clipledger, args.output)
        else:
            result = add_feedback(args.ledger, args.output, args)
        print(json_text(result), end="")
        return 0
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(json_text({"error": str(exc)}), end="")
        return 1
