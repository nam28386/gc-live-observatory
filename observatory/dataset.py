"""Extract a versioned H-GC-001 candidate dataset from attested local captures."""

import argparse
from collections import Counter
import json
import sqlite3
from pathlib import Path

from .replay import canonical, capture_prefix
from .web import software_revision

SECOND = 10**9
FEATURE_VERSION = "hgc001-receive-time-v4"
PRIMARY_METRIC = "paired holdout mean squared error difference (baseline minus augmented), impact_ticks squared"
MAX_RECORDS = 1_000_000
MAX_POPULATION = 100_000


def eligible(card):
    if card["phase"] != "outcome" or card.get("status") != "ELIGIBLE_AT_CUTOFF" or not card.get("label"):
        return "outcome_missing_or_invalid"
    if set(("pre_mid_ticks", "pre_spread_ticks", "pre_depth", "signed_contracts_1s")) - card.keys():
        return "baseline_missing"
    try:
        start = int(card["start"]["monotonic_ns"])
        t2, t30 = start + 2 * SECOND, start + 30 * SECOND
        prediction, label = card["prediction"], card["label"]
        if (int(prediction["cutoff_receive_ns"]) != t2
            or int(prediction["impact_sample_receive_ns"]) > t2
            or (prediction["last_refill_observation_receive_ns"] is not None and
                not start <= int(prediction["last_refill_observation_receive_ns"]) <= t2)
            or any(prediction[key] is None for key in ("impact_ticks", "refill_contracts", "opposite_refill_contracts"))
            or not t2 <= int(label["sample_receive_ns"]) <= t30
            or int(label["available_receive_ns"]) <= t30):
            return "clock_or_measurement_contract"
    except (KeyError, TypeError, ValueError):
        return "clock_or_measurement_contract"
    return None


def build_dataset(path, sessions, *, ledger=None):
    from hashlib import sha256

    population = []
    exclusions = Counter()
    prefixes = []
    retained = 0
    for session in dict.fromkeys(sessions):
        with capture_prefix(path, session, strict=True) as (provenance, position, _, rows):
            latest = {}
            digest = sha256()
            for record in rows:
                retained += 1
                if retained > MAX_RECORDS:
                    raise ValueError("Dataset input exceeds the one-million-record bound")
                digest.update((canonical(record) + "\n").encode())
                data = record["payload"]
                if record["kind"] == "evidence" and data.get("category") == "aggressive_flow_burst":
                    latest[data["id"]] = data
                    if len(latest) + len(population) > MAX_POPULATION:
                        raise ValueError("Dataset event population limit exceeded")
            prefixes.append({"session": session, "through_id": position, "sha256": digest.hexdigest(),
                             "source": provenance.get("source"), "producer_revision": provenance.get("software_revision")})
        for card in latest.values():
            reason = eligible(card)
            if card.get("capture_session") not in (None, session):
                reason = "capture_session_mismatch"
            if ledger is not None:
                ledger.append({"event_id": card["id"], "capture_session": session, "through_id": position,
                               "status": card["status"], "exclusion": reason, "phase": card["phase"]})
            if reason:
                exclusions[reason] += 1
                continue
            prediction, label = card["prediction"], card["label"]
            utc = int(card["start"]["utc_ns"])
            # Wall time orders capture groups. Eligibility and horizon checks use
            # only monotonic times from the same recorded process/session.
            population.append({
                "event_id": card["id"], "capture_session": session, "through_id": position,
                "exchange": card.get("exchange"), "instrument": card["instrument"], "expiry": card.get("expiry"),
                "source": card["source"], "clock": "collector_monotonic", "feature_version": FEATURE_VERSION,
                "t0_utc_ns": str(utc), "t0_receive_ns": card["start"]["monotonic_ns"],
                "coverage": "eligible_observed_mbp_and_bbo", "missing": card.get("missing_evidence", []),
                "feature_record_available_receive_ns": prediction["available_receive_ns"],
                "feature_materialized_receive_ns": prediction["available_receive_ns"],
                "consumer_visible_receive_ns": None,
                "availability_status": ("AFTER_CUTOFF" if
                    int(prediction["available_receive_ns"]) > int(prediction["cutoff_receive_ns"])
                    else "PUBLICATION_TIME_UNATTESTED"),
                "online_available_at_cutoff": False,
                "cutoff_receive_ns": prediction["cutoff_receive_ns"], "label": label,
                "baseline": {"signed_contracts_1s": card["signed_contracts_1s"], "spread_ticks": card["pre_spread_ticks"],
                             "fixed_pre_depth": card["pre_depth"], "pre_mid_ticks": card["pre_mid_ticks"], "utc_hour": card["utc_hour"],
                             "market_session": card.get("market_session", "UNKNOWN"), "impact_at_cutoff_ticks": prediction["impact_ticks"]},
                "added_refill_contracts": prediction["refill_contracts"],
                "negative_control_refill_contracts": prediction["opposite_refill_contracts"],
                "sample_times": {
                    "impact_receive_ns": prediction["impact_sample_receive_ns"],
                    "last_anchor_update_receive_ns": prediction["last_refill_observation_receive_ns"],
                },
            })
    population.sort(key=lambda item: (int(item["t0_utc_ns"]), item["event_id"]))
    groups = list(dict.fromkeys(item["capture_session"] for item in population))
    holdout = groups[-1] if len(groups) >= 2 else None
    boundary = min((int(item["t0_utc_ns"]) for item in population if item["capture_session"] == holdout), default=None)
    result = []
    ledger_index = {(row["capture_session"], row["event_id"]): row for row in ledger or ()}
    for item in population:
        if not holdout:
            item["split"] = "unassigned_one_group"
        elif item["capture_session"] == holdout:
            item["split"] = "holdout"
        elif int(item["t0_utc_ns"]) + 60 * SECOND >= boundary:
            reason = "overlap_or_30s_embargo"
            exclusions[reason] += 1
            key = item["capture_session"], item["event_id"]
            if key in ledger_index:
                ledger_index[key]["exclusion"] = reason
            continue
        else:
            item["split"] = "exploration"
        result.append(item)
    dataset_digest = sha256()
    for row in result:
        dataset_digest.update((canonical(row) + "\n").encode())
    manifest = {
        "schema_version": 1, "feature_version": FEATURE_VERSION, "hypothesis": "H-GC-001",
        "scientific_status": "UNRESOLVED", "primary_metric_predeclared": PRIMARY_METRIC,
        "population": "finalized aggressive_flow_burst evidence with anchored depth, valid coverage and time-eligible quote samples",
        "prediction_horizon_seconds": 2, "label_horizon_seconds": 30, "embargo_seconds": 30,
        "split": "whole capture sessions, latest eligible group reserved, purge exploration labels overlapping holdout plus embargo",
        "capture_prefixes": prefixes, "dataset_revision": software_revision(), "records_examined": retained,
        "eligible_rows": len(result), "exclusions": dict(exclusions),
        "dataset_sha256": dataset_digest.hexdigest(),
        "timing_limitations": [
            "Prediction samples exclude later observations; the event-driven feature record may materialize after t0+2s.",
            "UTC is used for ordering across sessions. Monotonic timestamps are only compared inside one session.",
        ],
    }
    return result, manifest


def main():
    from .__main__ import local_path

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("database", type=Path)
    cli.add_argument("output_directory", type=Path, help="new local directory outside Git")
    cli.add_argument("--session", required=True, action="append")
    args = cli.parse_args()
    try:
        directory = local_path(args.output_directory)
        ledger = []
        rows, manifest = build_dataset(args.database, args.session, ledger=ledger)
        manifest["population_ledger_rows"] = len(ledger)
        directory.mkdir()
        with (directory / "dataset.jsonl").open("x", encoding="utf-8", newline="\n") as output:
            for row in rows:
                output.write(canonical(row) + "\n")
        with (directory / "population.jsonl").open("x", encoding="utf-8", newline="\n") as output:
            for row in ledger:
                output.write(canonical(row) + "\n")
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, sqlite3.Error) as exc:
        cli.exit(1, f"Dataset extraction failed: {exc}\n")


if __name__ == "__main__":
    main()
