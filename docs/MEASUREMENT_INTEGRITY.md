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

## Phase 02 measurement contract

Phase 02 pins the [API](https://github.com/fxaurum/rithmic-api-docs/blob/581cb6b3706c93ccd1667b32234e904654a44728/API.md) and [MBO](https://github.com/fxaurum/rithmic-api-docs/blob/581cb6b3706c93ccd1667b32234e904654a44728/MBO.md) at the same revision. MBP is absolute size by side and price. A Reset requires an Image, and deltas must be contiguous within the observed generation. Gaps and repair Images start new coverage epochs. A snapshot plus buffered replay can restore the book. Replayed deltas never restore historical OFI. A crossed, incomplete or stale book does not publish eligible liquidity measures.

| Feature | Measurement and eligibility |
| --- | --- |
| Trade flow | Two collector-monotonic seconds of eligible prints. Buy and sell volume sum known signs in contracts, net is their difference. Unknown volume remains separate. CVD spans observed eligible trades in the process. Response is the first-to-last eligible print change in ticks divided by nonzero signed flow. Rate is count divided by window seconds. Partial or saturated windows are labeled. |
| OFI | At each of five ranks, bid improvements add new size, worsening bids remove old size, stable levels use size change. Ask contributions use the opposite sign. Sum by rank and across ranks over two receive-time seconds. Price-fixed changes use the changed level's absolute size difference. Boundary changes count changed ranked prices. Normalization divides by current displayed top-five depth. Missing continuity or window saturation suppresses aggregates. |
| Liquidity | Spread and price distance are ticks. Top-five depth and best sizes are displayed contracts. Imbalance is bid minus ask over total. Concentration is the sum of squared level shares. BBO comparison requires fresh receipt timestamps within 250 ms. Unavailability fraction is sampled on observations, at most ten times per second, rather than time weighted. |
| Refill | Hold pre-event top-five price ticks fixed. Positive size changes are observed additions and negative changes are depletion. Measure same-side retention and the first replenishment returning the attacked-side depth to at least half its anchor baseline. Opposite-side anchored additions are the comparison feature. No attribution to replenishing orders or to voluntary cancellations. |
| MBO | `q` on Fill is incremental, `k/ki` are cumulative for the price leg. Preserve exact and inferred counts separately. Resting size follows order updates only. Delete attribution is unknown. An Image observation is left-censored. Survival is Kaplan–Meier on the first 512 observed New IDs in a valid local epoch, with ongoing IDs right-censored, snapshots excluded and unsupported horizons missing. This is survival of observed IDs and supplies no exact queue position. |
| Intensity | Observed category counts per two receipt-time seconds. The window can be partial. Missing depth, DBO or transport is never filled with synthetic events in LIVE. |
| Candidate cards | Fixed synthetic QA thresholds select flow bursts, depth contractions, weak-flow price moves, high observed event intensity and eligible post-burst refill/recovery/response observations. Thresholds are engineering defaults and lack a calibrated market baseline. Alternatives are hypotheses. |

### H-GC-001

The observation starts on an eligible signed-flow burst and stores the pre-event BBO and fixed MBP grid, capture/session ID when recorded, source revision and market clock context. Prediction inputs use observations no later than `t0+2s` on the collector monotonic clock. The baseline includes signed flow, spread, pre-depth, UTC hour, and the last sufficiently fresh mid at the cutoff relative to the pre-event mid. An exchange session label is unknown because there is no exchange calendar in the feature layer. Anchored refill is measured separately for the attacked side and opposite side.

An event after the cutoff materializes the record and the journal records its actual availability separately. The `t0+30s` label is first materialized only after observation passes the horizon and uses the last sufficiently fresh mid at or before the horizon. Transport interruption, MBP reinitialization, sequence gaps, stale coverage or invalid BBO exclude the window. A recovery never changes the eligibility of an earlier failed window. Replay consumes a selected session in durable order with timestamp and sample bounds. It requires an intact retained prefix for parity.

The scientific result remains **UNRESOLVED**. Cross-stream timing, hidden liquidity, trade-side classification and common causes limit identification. No actor, profitability or causal inference is established.

## Phase 04 wave and response contract

Wave classification is a receive-time observation over the existing feature engine. Observe eligible trade flow, BBO displacement and MBP coverage. The opening observation copies only candle fields known at that point and records whether the candle is forming or closed as of receipt. H1/M15/M5/M1 have separate horizons and may link to a compatible observed parent. A missing M1 bucket, known session change or coverage interruption invalidates affected active classifications. The old transition remains in the history.

| State | Observable threshold in observed-wave-v1 |
| --- | --- |
| Initiation candidate | Existing one-second signed-flow burst and a fresh quoted midpoint |
| Propagation observed | Mid displaced at least two ticks in the signed-flow direction |
| Acceptance candidate | Later observed quote retains at least two ticks of displacement after the horizon-specific confirmation delay |
| Stall observed | Confirmation window observed without two ticks of displacement |
| Exhaustion candidate | Two ticks of observed decay from the observed directional peak |
| Reversal observed | Opposing displacement of at least two ticks relative to the initiation anchor |
| Invalidated or ended | Lost coverage, verified session boundary, new observed exchange candle or elapsed horizon |

The quote sample triggers are observations. They do not determine continuity between quotes, identify a market participant, or classify a final swing using future prices. Mode A can record partial waves without MBP. A recorded transition becomes visible after the same durable publication boundary as its input. Raw journal receipt timestamps are distinct from exchange source times.

Canonical objects keep facts, measurements, hypotheses and missing evidence separate. Contradictory observations do not become proof that a particular actor mechanism is rejected. The bounded dashboard index shows `EXPECTED_RESPONSE_UNAVAILABLE` and a null residual without an eligible empirical reference. Hypotheses such as passive replenishment, ordinary price discovery and cross-stream timing remain alternatives.

H-GC-001 extraction preserves the complete candidate population ledger, including excluded candidates. Offline inputs are constrained by the original +2 s cutoff and their observed sample times. Materialization is separate from consumer visibility. An after-cutoff record is marked `AFTER_CUTOFF`; a publication time that was not individually attested remains null. The locked evaluation uses training-only standardization and fixed ridge penalty. Baseline, anchored-refill and opposite-side-control models share the same rows. The primary metric is paired MSE difference on the purged chronological session holdout. A single holdout session leaves interval uncertainty unestimated. Synthetic fixtures exercise mechanics only. The scientific status remains `UNRESOLVED`.
