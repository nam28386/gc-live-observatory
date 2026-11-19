# PR00 data and runtime contracts

## Source and coverage

The executable mode is `MOCK`. Its fixed fixture instrument is `COMEX.GCZ6` with a 0.1 tick grid. A single local collector consumes two allowlisted channels over a synthetic WebSocket. FastAPI and the synthetic gateway bind to loopback. The collector bypasses HTTP proxies for its loopback WebSocket and rejects the Windows gateway port 9001. The browser receives snapshots only and cannot submit gateway methods.

The implementation pins the [Fxaurum API reference](https://github.com/fxaurum/rithmic-api-docs/blob/581cb6b3706c93ccd1667b32234e904654a44728/API.md) for `SUBSCRIBE`, `@trade` and `@bookTicker` shapes. Payloads must match both the expected stream and contract symbol. A matching success ACK activates collection. Pre-ACK frames are buffered up to 32 events, with an explicit error and retry on overflow. The WebSocket frame queue and frame size are bounded. The dashboard receives periodic state snapshots and keeps no subscriber event backlog.

Client socket loss ends the current capture segment. A new observation after a fresh ACK begins the next segment. Gap endpoints are collector observations, so they describe the detected interruption and observed resumption. Silence with an open socket yields waiting, silent or stale states based on a monotonic clock. Silence alone cannot establish a cause, upstream disconnection or market closure. Candle history retains up to 120 observed bars per timeframe. Coverage and gap history retain up to 64 records each, with a cumulative gap count for the process. All state is in memory.

## Time and trade eligibility

- The event contract preserves `T` in milliseconds, `tu` in microseconds, optional `tn` as a nanosecond remainder and `r` in milliseconds. Collector UTC and monotonic nanoseconds are separate and serialized as decimal strings. The mock exchange clock is virtual.
- `@bookTicker` observations use collector time. The quote channel can carry metadata and a last-price snapshot. `vt` and `oit` are independent volume and OI source times and have their own validity flags. Repeated prices and sizes leave the BBO-change clock unchanged. A first observation after subscribe or after an invalid quote establishes a baseline. A valid size-only change also moves the BBO-change clock. Crossed or unavailable quotes suppress the current BBO and spread.
- OHLCV uses eligible `@trade` prints and UTC bucket floors. A later eligible trade finalizes earlier buckets. The first observed bucket per timeframe and the bars adjoining a known gap are partial. Empty buckets are left blank. Trades behind the last accepted exchange millisecond are counted as late and excluded, with the affected retained bar marked partial. Multiple prints at the same millisecond are accepted because that millisecond is not a print identity.
- Conditional prints are counted separately. Unknown aggressor prints contribute eligible OHLCV and zero **signed** volume. CVD represents the cumulative signed volume actually observed by the process. Transport errors and partial capture limit what it represents.

## Windows live handoff

Run the synthetic quickstart and regression suite on Windows first. Keep Fxaurum on its Windows loopback. Once a local live adapter exists, validate the following in an active market session.

1. On Windows, open Fxaurum and verify the market-data connection and actual GC contract and expiry.
2. Run `Test-NetConnection 127.0.0.1 -Port 9001` locally. Run the read-only live command documented by that adapter.
3. Verify a correlated subscribe ACK, observed BBO and a new trade for the expected instrument. Inspect source and observation timestamps, candle behavior and coverage.
4. Record only non-sensitive diagnostic summaries. Keep feed captures, broker data and credentials on the Windows machine.

Windows live is `LIVE_UNVERIFIED` in PR00. A separate adapter and operator validation are prerequisites for Windows live acceptance. Exchange entitlement and usage rights require independent confirmation.

## Future contracts

- **PR02 · MBP.** Track generation and sequence. A recovery image with `src=1` reestablishes book state while historical gaps remain in coverage. OFI and refill windows crossing missing event history stay ineligible.
- **PR03 · DBO.** The gateway owns Fill generation. Preserve exact passive-ID matches (`src=0`) and inferred fills (`src=1`) separately and avoid independently joining Trade to DBO. Verify DBO entitlement on Windows.
- **H-GC-001.** Set the prediction cutoff at `t+2s`. Compare against a baseline including observed price impact at that cutoff. The incremental feature is refill at a fixed pre-event price grid over `[t,t+2s]`. Predict retained impact at `t+30s` and use opposite-side refill as a negative control with its assumptions documented. Synthetic evidence leaves the scientific verdict `UNRESOLVED`.
