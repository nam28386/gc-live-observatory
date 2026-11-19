# Release gates

| Gate | Evidence required |
| --- | --- |
| SOURCE_RECOVERED | Phase 01 descends from the exact PR00 SHA and remote refs are read back after publication. |
| MOCK_REGRESSION_PASS | The original suite and the extended synthetic suites run with Chromium where available. |
| LIVE_CONNECTOR_CODE_READY | Synthetic vendor-profile adapter tests exercise subscription, strict stream selection, shared normalization, quality, and loopback restrictions. |
| LOCAL_RECORDING_VERIFIED | Synthetic tests verify durability, crash restart, coverage history, retention and failure handling. |
| WINDOWS_OPERATOR_READY | PowerShell scripts match the Python CLI and run on Windows. Until run on Windows, report PARTIAL. |
| CI_VERIFIED | Ubuntu and Windows Actions results are confirmed for the actual source SHA. |
| WINDOWS_LIVE_VALIDATED | The local operator confirms the gateway, contract expiry, matched ACK, eligible live Trade/BBO and quality using the smoke procedure. |

Engineering acceptance uses synthetic data. The operator verifies real market data separately.
