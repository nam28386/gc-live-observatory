"""Locked offline H-GC-001 ridge evaluation for permitted, attested local data."""

import argparse
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean, pstdev

from .dataset import FEATURE_VERSION, PRIMARY_METRIC, MAX_POPULATION, SECOND
from .replay import canonical

MODEL = "ridge-fixed-1-v1"
BASELINE = "paired-hgc001-impact-v1"
MIN_TRAIN = 30
MIN_HOLDOUT = 10
MIN_TRAIN_SESSIONS = 2


def vector(row, extension=None):
    base = row["baseline"]
    values = [base["signed_contracts_1s"], base["spread_ticks"],
              sum(level[1] for side in base["fixed_pre_depth"] for level in side),
              base["pre_mid_ticks"], base["utc_hour"], base["impact_at_cutoff_ticks"]]
    if extension:
        values.append(row[extension])
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError("Non-finite or missing feature")
    return values


def solve(gram, target):
    matrix = [row[:] + [value] for row, value in zip(gram, target, strict=True)]
    for k, row in enumerate(matrix):
        denom = row[k]
        for j in range(k, len(row)):
            row[j] /= denom
        for i, other in enumerate(matrix):
            if i == k:
                continue
            factor = other[k]
            for j in range(k, len(row)):
                other[j] -= factor * row[j]
    return [row[-1] for row in matrix]


def fit(train_x, train_y):
    centers = list(map(mean, zip(*train_x, strict=True)))
    scales = [pstdev(values) or 1 for values in zip(*train_x, strict=True)]
    bias = mean(train_y)

    def transform(row):
        return [(x - center) / scale for x, center, scale in zip(row, centers, scales, strict=True)]

    design = [transform(row) for row in train_x]
    columns = list(zip(*design, strict=True))
    gram = [[sum(a * b for a, b in zip(x, z, strict=True)) + (i == j)
             for j, z in enumerate(columns)] for i, x in enumerate(columns)]
    target = [sum(x * (y - bias) for x, y in zip(column, train_y, strict=True)) for column in columns]
    weights = solve(gram, target)

    def predict(row):
        return bias + sum(a * b for a, b in zip(weights, transform(row), strict=True))

    return predict


def score_models(train, holdout):
    train_y = [row["label"]["impact_ticks"] for row in train]
    holdout_y = [row["label"]["impact_ticks"] for row in holdout]
    if any(type(y) not in (int, float) or not math.isfinite(y) for y in train_y + holdout_y):
        raise ValueError("Non-finite outcome")
    per_session = {}
    metrics = {}
    for name, feature in (("baseline", None), ("anchored", "added_refill_contracts"),
                          ("control", "negative_control_refill_contracts")):
        predict = fit([vector(row, feature) for row in train], train_y)
        errors = [(predict(vector(row, feature)) - y) ** 2 for row, y in zip(holdout, holdout_y, strict=True)]
        metrics[name] = mean(errors)
        for row, error in zip(holdout, errors, strict=True):
            per_session.setdefault(row["capture_session"], {}).setdefault(name, []).append(error)
    return {
        "holdout_mse": metrics, "paired_difference": metrics["baseline"] - metrics["anchored"],
        "negative_control_difference": metrics["baseline"] - metrics["control"],
        "by_capture_session_mse": {key: {name: mean(errors) for name, errors in scores.items()}
                                    for key, scores in per_session.items()},
        "uncertainty_interval": None, "uncertainty_note": "One locked holdout session; interval unavailable",
        "incremental_helpful_on_sample": metrics["anchored"] < metrics["baseline"],
    }


def evaluate(manifest, rows, *, confirm_permitted=False):
    digest = sha256()
    for row in rows:
        digest.update((canonical(row) + "\n").encode())
    if manifest.get("dataset_sha256") != digest.hexdigest() or manifest.get("primary_metric_predeclared") != PRIMARY_METRIC:
        raise ValueError("Dataset digest or primary metric mismatch")
    if len({row["event_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate research event")
    train = [row for row in rows if row["split"] == "exploration"]
    holdout = [row for row in rows if row["split"] == "holdout"]
    train_groups, test_groups = (Counter(row["capture_session"] for row in group) for group in (train, holdout))
    if train_groups.keys() & test_groups.keys() or len(test_groups) > 1:
        raise ValueError("Expected disjoint chronological sessions and one locked holdout")
    if train and holdout and (
        max(int(row["t0_utc_ns"]) + 60 * SECOND for row in train) >= min(int(row["t0_utc_ns"]) for row in holdout)
    ):
        raise ValueError("Purge or embargo integrity failed")
    result = {
        "scientific_status": "UNRESOLVED", "baseline_version": BASELINE, "model": MODEL,
        "dataset_sha256": digest.hexdigest(), "metric": PRIMARY_METRIC,
        "training_rows": len(train), "holdout_rows": len(holdout), "exclusions": manifest.get("exclusions", {}),
        "availability": "OFFLINE_AS_OF_ONLY", "empirical_evaluation": "NOT_RUN_INSUFFICIENT_DATA",
    }
    prefix_sources = {prefix["session"]: prefix["source"] for prefix in manifest.get("capture_prefixes", [])}
    if not rows or any(
        row["source"] != "fxaurum_rithmic" or prefix_sources.get(row["capture_session"]) != "fxaurum_rithmic"
        for row in rows
    ):
        return {**result, "reason": "No eligible attested real-only population"}
    if not confirm_permitted:
        return {**result, "empirical_evaluation": "NOT_RUN_PERMISSION_REQUIRED"}
    if len(train) < MIN_TRAIN or len(holdout) < MIN_HOLDOUT or len(train_groups) < MIN_TRAIN_SESSIONS:
        return {**result, "reason": "Insufficient chronological training sessions or holdout events"}
    prefix_positions = {prefix["session"]: prefix["through_id"] for prefix in manifest["capture_prefixes"]}
    if manifest.get("feature_version") != FEATURE_VERSION:
        raise ValueError("Unsupported feature version")
    for row in train + holdout:
        start = int(row["t0_receive_ns"])
        cutoff = int(row["cutoff_receive_ns"])
        impact = int(row["sample_times"]["impact_receive_ns"])
        refill = row["sample_times"]["last_anchor_update_receive_ns"]
        label = row["label"]
        if (row["feature_version"] != FEATURE_VERSION or prefix_positions[row["capture_session"]] != row["through_id"]
                or cutoff != start + 2 * SECOND or impact > cutoff
                or (refill is not None and not start <= int(refill) <= cutoff)
                or not cutoff <= int(label["sample_receive_ns"]) <= start + 30 * SECOND
                or int(label["available_receive_ns"]) <= start + 30 * SECOND):
            raise ValueError("Event time, label availability or durable prefix mismatch")
    return {**result, "empirical_evaluation": "EXECUTED_LOCKED_HOLDOUT", **score_models(train, holdout)}


def main():
    from .__main__ import local_path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="local directory produced by observatory.dataset")
    parser.add_argument("--confirm-permitted-data", action="store_true")
    args = parser.parse_args()
    try:
        directory = local_path(args.dataset)
        publication = directory / "locked-evaluation.json"
        if publication.exists():
            raise ValueError("This directory already has a locked holdout report")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        rows = []
        with (directory / "dataset.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if len(rows) == MAX_POPULATION or len(line) > 100_000:
                    raise ValueError("Dataset input exceeds bounded evaluation limits")
                rows.append(json.loads(line))
        # Acquire a single-writer holdout lease before calculating results.
        with (directory / ".holdout-lease").open("x"):
            try:
                result = evaluate(manifest, rows, confirm_permitted=args.confirm_permitted_data)
                if result["empirical_evaluation"] == "EXECUTED_LOCKED_HOLDOUT":
                    with publication.open("x", encoding="utf-8") as output:
                        json.dump(result, output, indent=2)
                print(json.dumps(result, indent=2))
            finally:
                (directory / ".holdout-lease").unlink()
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(1, f"Evaluation failed: {exc}\n")


if __name__ == "__main__":
    main()
