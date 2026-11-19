from copy import deepcopy
from hashlib import sha256
import json
from zipfile import ZipFile

import pytest

from observatory.pilot import build_report, package_identity, resources

SHA = "a" * 40
ID = "b" * 32


def diagnostic():
    return {
        "schema_version": 1, "git_sha": SHA, "source": "LIVE", "os": "Windows", "python": "3.12.4",
        "effective_mode": "B", "requested_mode": "C", "timestamp_utc": "2026-11-20T10:00:00+07:00",
        "contract": {"exchange": "COMEX", "symbol": "GCZ6", "expiry": "2026-12-29"},
        "expiry_verification": "OPERATOR_ATTESTED", "gateway_tcp_reachable": True,
        "recorder": {"session_id": None, "durable_position": None, "enabled": False},
        "gates": {"subscribe_ack": "PASS", "trade": "PASS", "bbo": "PASS", "mbp": "PASS",
                  "dbo_observation": "UNVERIFIED", "capture_io": "NOT_APPLICABLE"},
        "receiver": {"uncertain_received_frames": 0, "receive_queue_high_water": 16,
                     "raw": {"price": "private-value"}},
        "quality": {"transport_gap_count": 0, "mbp_recovery_count": 0},
    }


def test_pilot_proves_package_identity_and_keeps_fallback_evidence_separate(tmp_path):
    archive = tmp_path / "source.zip"
    with ZipFile(archive, "w") as package:
        package.writestr("RELEASE.json", json.dumps({"source_sha": SHA}))
    checksum = sha256(archive.read_bytes()).hexdigest()
    report = build_report(diagnostic(), ID, package_identity(archive, checksum), checksum, authorized=True)
    assert report["artifact_scope"] == "INNER_SOURCE_ZIP"
    assert report["market_data_mode"] == {"requested": "C", "effective": "B"}
    assert report["mbp_status"] == {"status": "PASS", "basis": "SYSTEM_OBSERVED"}
    assert report["pilot_result"] == "PARTIAL"
    assert report["independent_field_verification"] == "UNVERIFIED"
    assert report["workstation_authorization"] == "OPERATOR_ATTESTED"
    assert "private-value" not in json.dumps(report)
    with pytest.raises(ValueError, match="checksum mismatch"):
        package_identity(archive, "a" * 64)
    bad = deepcopy(diagnostic())
    bad["timestamp_utc"] = "2026-11-20T10:00:00"
    with pytest.raises(ValueError, match="timezone"):
        build_report(bad, ID, SHA, checksum)


def test_resource_sample_requires_same_session_and_ends_before_diagnostic():
    from observatory.pilot import timestamp
    sample = dict(schema_version=1, source_sha=SHA, session_id=ID,
                  started_utc="2026-11-20T02:58:00Z", ended_utc="2026-11-20T02:59:00Z",
                  elapsed_seconds=60, cpu_seconds=2, peak_sampled_working_set_bytes=310_000,
                  capture_bytes_start=10000, capture_bytes_end=23000,
                  volume_free_bytes_start=100000, volume_free_bytes_end=87000, samples=120,
                  received_frames_delta=160, durable_market_records_delta=120, uncertain_receipts_delta=0,
                  username="private-value")
    point = timestamp("2026-11-20T10:00:00+07:00")
    result = resources(sample, {"session_id": ID}, SHA, point)
    assert result["elapsed_seconds"] == 60 and "username" not in result
    assert result["durable_market_records_per_second"] == 2
    with pytest.raises(ValueError, match="capture session"):
        resources(sample, {"session_id": "0" * 32}, SHA, point)
    with pytest.raises(ValueError, match="window"):
        resources(sample, {"session_id": ID}, SHA, timestamp("2026-11-20T02:58:59Z"))
