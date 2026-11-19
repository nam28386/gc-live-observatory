# Phase 01 measurement integrity

| Measurement | Clock and eligibility |
| --- | --- |
| Trade event time | `T` UTC ms. `tu` is separately preserved in µs and `tn` is a ns remainder. `r` is gateway reception ms. |
| Application observed time | Wall clock UTC ns and monotonic ns are recorded separately when a frame is read by the collector. They can include queueing delay. Use the monotonic observation clock for freshness within a process. |
| Quote | Observe bid, ask, sizes and changed price/size separately from BBO message arrival. `vt` and `oit` are source times for session volume and OI. Each has its own validity and update clock. |
| Last reported BBO price | `bookTicker.l` may come from a snapshot. Only eligible Trade frames update bars and signed flow. |
| Candles and signed flow | UTC buckets of observed `tc=0` trades. Unknown side counts in OHLCV with zero signed flow. Late trades are excluded from aggregates, counted and marked in the journal. LIVE rejects trade clocks more than five seconds ahead of collector UTC. |
| Coverage | The first observed bar and bars adjoining a known interruption are partial. The next eligible trade in a later bucket finalizes the previous observed bar. Missing buckets stay empty. |
| Restarts | A new session gets its own monotonic basis. The prior durability boundary and first subsequent observation bracket uncertainty, and cannot locate all missing exchange events. |

A crossed or unavailable quote cannot supply a spread. A trade or quote silence may reflect inactivity, a closed session or upstream failure. No exchange session calendar or complete feed sequence is assumed. Expiry is operator-confirmed metadata, checked against the symbol month/year. It is independent of the observed venue/symbol check.
