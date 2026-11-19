# Repository guide

- Run the Python 3.12 setup, mock app and tests from the repository root using the commands in [README](README.md).
- The mock gateway, collector, event types and state engine are in `observatory/`. The FastAPI app and static dashboard use the same snapshots. The tests include real loopback WebSockets.
- [PR00 contracts](docs/PR00_CONTRACTS.md) define timing, data quality and security scopes. Keep Cloud and CI synthetic, and keep the Windows gateway local and read-only.
- Use the [playbook](docs/GC_LIVE_OBSERVATORY_CODEX_CLOUD_PLAYBOOK_2026-11-19.md) to scope each PR and the [master prompt](docs/GC_CODEX_CLOUD_MASTER_PROMPT_2026-11-19.md) for future release acceptance.
