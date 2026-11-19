# Phase 01 architecture

Windows LIVE connects to the explicit IPv4 loopback Fxaurum port. MOCK starts its own ephemeral synthetic gateway. Both use the same allowlisted subscription, typed parser, capture state, FastAPI API and four chart views. The CLI forbids LIVE on Linux. Tests exercise the LIVE adapter only against synthetic sockets.

```text
Fxaurum loopback or synthetic gateway
  -> bounded WebSocket frame queue -> correlated ACK -> typed parser
  -> optional durable local journal -> shared Trade/BBO state
  -> loopback FastAPI HTTP/WS -> dashboard
```

The collector subscribes only to the configured COMEX GC contract's Trade and BBO. It never accepts a method from the HTTP or dashboard WebSocket. A transient upstream pause leaves the socket connected. Socket loss initiates a fresh subscription. Failed connection or subscription attempts back off to a maximum of five seconds. Live HTTP validates Host and live browser WebSocket validates Origin. Keep the vendor socket and application bound to loopback.

## Local journal

Opt in with `--capture <database>` or PowerShell `-Capture` after confirming rights. SQLite WAL with synchronous FULL commits each normalized event and its allowlisted raw frame before publishing it into app state. A coverage marker and its triggering event share a transaction. Invalid input diagnostics are counted. Rejected fields passed to the normalizer can be recorded with a reason and without the raw payload. ACK and interruption markers carry the request ID. Stored provenance includes source, configured COMEX contract/expiry and pinned protocol documentation revision.

An OS lock grants a database one writer. The collector waits for its transaction to commit before consuming another frame, so the application has at most one write in flight and uses the bounded WebSocket queue for backpressure. Size and pre-ACK queue overflows are counted and recorded as interruptions. A disk error stops capture and collection and surfaces in the dashboard. Full throughput on a user's Windows disk is unmeasured.

Each boot creates a session UUID. A crash leaves any incomplete transaction uncommitted. On restart the journal detects an unclosed session and adds a restart gap from its last durable timestamp to restart detection. The next qualifying Trade/BBO observation begins coverage. History persists across restarts. In-memory candle rendering begins a new observed run.

`GET /api/history?after=0&limit=100` returns ascending durable records. Page with the last record ID. The limit is 250. `gaps=true` returns the newest restart, disconnect and coverage markers first. The history includes schema version, session ID, observed UTC nanoseconds, record kind and provenance. Retention defaults to seven days and runs at startup and on sustained writes. Runs retain insertion order and remove rows only under retention. Use a private local directory outside Git and cloud sync.
