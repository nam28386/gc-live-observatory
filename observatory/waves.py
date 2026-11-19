"""Point-in-time wave observations over the existing collector measurements."""
from collections import deque
from dataclasses import dataclass, field
from hashlib import sha256

NS = 1_000_000_000
HORIZONS = {"H1": 3600, "M15": 900, "M5": 300, "M1": 60}
CONFIRM = {"H1": 15, "M15": 5, "M5": 2, "M1": .5}
TERMINAL = {"REVERSAL_OBSERVED", "INVALIDATED", "ENDED"}
VERSION = "observed-wave-v1"


@dataclass
class Wave:
    header: dict
    anchor: float
    direction: int
    birth: int
    peak: float = 0
    displaced_at: int | None = None
    transitions: list[dict] = field(default_factory=list)

    def wire(self):
        last = self.transitions[-1]
        return {
            **self.header, "state": last["state"], "active": last["state"] not in TERMINAL,
            "end": None if last["state"] not in TERMINAL else last["observed"],
            "transitions": list(self.transitions),
        }


class Waves:
    def __init__(self, instrument, source):
        self.instrument, self.source = instrument, source
        self.active = {}
        self.closed = deque(maxlen=32)
        self.durable = []
        self.latest_m1 = None
        self.prior_session = None
        self.flow = 0
        self.transitions = 0

    @staticmethod
    def price_context(bars):
        contexts = {}
        for name in HORIZONS:
            series = bars.get(name, ())
            if not series:
                continue
            bar = series[-1]
            contexts[name] = {
                "start_ms": bar.start_ms, "end_ms": bar.end_ms,
                "open_ticks": bar.open_ticks, "last_observed_ticks": bar.close_ticks,
                "bar_state": "CLOSED_AS_OF" if bar.finalized else "FORMING_AS_OF",
                "partial": bar.partial,
            }
        return contexts

    def trade_context(self, observed, bars, session):
        series = bars.get("M1", ())
        if series:
            current = series[-1].start_ms
            if self.latest_m1 is not None and current > self.latest_m1 + 60_000:
                self.invalidate(observed, "Missing M1 buckets between observed trades")
            self.latest_m1 = current
        if session and self.prior_session and session != self.prior_session:
            self.invalidate(observed, "Observed exchange-session boundary")
        if session:
            self.prior_session = session
        if session == "CLOSED":
            self.invalidate(observed, "Verified exchange session closed")
        for name, wave in tuple(self.active.items()):
            bar = bars.get(name, ())
            if bar and bar[-1].start_ms != wave.header["bar"]["start_ms"]:
                self.transition(wave, observed, "ENDED", "Next exchange candle observed",
                                {"next_bar_start_ms": bar[-1].start_ms})

    def start(self, card, observed, bars, session):
        anchor = card["pre_mid_ticks"]
        if anchor is None or session == "CLOSED":
            return
        direction = 1 if card["signed_contracts_1s"] > 0 else -1
        parent = None
        self.flow = card["signed_contracts_1s"]
        for name, bar in self.price_context(bars).items():
            if name in self.active:
                candidate = self.active[name]
                parent = candidate.header["wave_id"] if candidate.direction == direction else None
                continue
            # Opposing observed price structure cannot act as a parent wave.
            movement = direction * (bar["last_observed_ticks"] - bar["open_ticks"])
            if name != "M1" and movement < 0:
                parent = None
                continue
            wave_id = sha256(f"{card['id']}:{name}:{VERSION}".encode()).hexdigest()[:24]
            header = {
                "wave_id": wave_id, "parent_wave_id": parent, "capture_session": card["capture_session"],
                "event_ids": [card["id"]], "source": self.source, "instrument": self.instrument.symbol,
                "exchange": self.instrument.exchange, "expiry": self.instrument.expiry,
                "start": observed.wire(), "trade_exchange_ms": card["trade_exchange_ms"],
                "clock": "collector_monotonic", "timeframe": name,
                "direction": "UP" if direction == 1 else "DOWN",
                "horizon_seconds": HORIZONS[name], "bar": bar, "session": session or "UNKNOWN",
                "coverage": "PARTIAL" if bar["partial"] or not all(card["pre_depth"]) else "OBSERVED",
                "contract_version": VERSION,
            }
            wave = Wave(header, anchor, direction, observed.monotonic_ns)
            self.active[name] = wave
            self.transition(wave, observed, "INITIATION_CANDIDATE", "Observed one-second signed flow burst",
                            {"signed_contracts_1s": self.flow, "pre_mid_ticks": anchor,
                             "pre_spread_ticks": card["pre_spread_ticks"], "pre_depth": card["pre_depth"]})
            parent = wave_id

    def quote(self, observed, mid, spread, details=None):
        for name, wave in tuple(self.active.items()):
            stamp = observed.monotonic_ns
            elapsed = stamp - wave.birth
            response = wave.direction * (mid - wave.anchor)
            wave.peak = max(wave.peak, response)
            state = wave.transitions[-1]["state"]
            metrics = {"directional_mid_displacement_ticks": response,
                       "observed_peak_ticks": wave.peak, "spread_ticks": spread,
                       "last_sampled_signed_contracts_1s": self.flow}
            if elapsed >= HORIZONS[name] * NS:
                target, trigger = "ENDED", "Observed receive-time horizon elapsed"
            elif response <= -2:
                target, trigger = "REVERSAL_OBSERVED", "Mid crossed anchor by two opposing ticks"
            elif state in ("INITIATION_CANDIDATE", "STALL_OBSERVED") and response >= 2:
                wave.displaced_at = stamp
                target, trigger = "PROPAGATION_OBSERVED", "Mid reached two directional ticks"
            elif state == "INITIATION_CANDIDATE" and elapsed >= CONFIRM[name] * NS:
                target, trigger = "STALL_OBSERVED", "No two-tick displacement by observation window"
            elif state in ("PROPAGATION_OBSERVED", "ACCEPTANCE_CANDIDATE") and wave.peak - response >= 2:
                target, trigger = "EXHAUSTION_CANDIDATE", "Observed displacement decayed by two ticks from the peak"
            elif state == "PROPAGATION_OBSERVED" and response >= 2 and (
                stamp - wave.displaced_at >= CONFIRM[name] * NS
            ):
                target, trigger = "ACCEPTANCE_CANDIDATE", "Displacement retained at a later observed quote"
            else:
                continue
            if details:
                metrics.update(details())
            self.transition(wave, observed, target, trigger, metrics)

    def invalidate(self, observed, trigger):
        for wave in tuple(self.active.values()):
            self.transition(wave, observed, "INVALIDATED", trigger, {})

    def transition(self, wave, observed, state, trigger, measurements):
        stamp = observed.wire()
        prior = wave.transitions[-1]["state"] if wave.transitions else "UNDETERMINED"
        change = {
            "state": state, "previous_state": prior, "observed": stamp,
            "information_clock": "collector_observation; capture publication follows durable commit",
            "trigger": trigger, "measurements": measurements,
            "validity": "INELIGIBLE" if state == "INVALIDATED" else wave.header["coverage"],
            "supporting_evidence_ids": wave.header["event_ids"],
            "contradictory_evidence": (
                ["Displayed displacement decayed or moved against initiation"] if state in
                ("EXHAUSTION_CANDIDATE", "REVERSAL_OBSERVED") else []
            ),
            "uncertainty": "Observation only; quote sampling and anonymous flow limit attribution",
            "falsifier": "Later executable quotes fail to retain displacement or source coverage breaks",
        }
        wave.transitions.append(change)
        self.transitions += 1
        self.durable.append({"wave": wave.header, "transition": change})
        if state in TERMINAL:
            del self.active[wave.header["timeframe"]]
            self.closed.append(wave)

    def view(self):
        return [wave.wire() for wave in (*self.closed, *self.active.values())][-24:]


def research_object(wave, evidence):
    """Normalize observation and alternative mechanisms without unsupported forecasts."""
    last = wave["transitions"][-1]
    lookup = {record["id"]: record for record in evidence}
    linked = [lookup[key] for key in wave["event_ids"] if key in lookup]
    origin = linked[0] if linked else {}
    return {
        "research_object_id": f"wave-{wave['wave_id']}", "wave_id": wave["wave_id"],
        "parent_wave_id": wave["parent_wave_id"], "event_ids": wave["event_ids"],
        "source": wave["source"], "capture_session": wave["capture_session"],
        "contract": {"exchange": wave["exchange"], "symbol": wave["instrument"], "expiry": wave["expiry"]},
        "session": wave["session"], "source_time_ms": wave["trade_exchange_ms"],
        "observation": last["observed"], "information_clock": last["information_clock"],
        "feature_schema": VERSION, "pre_state": wave["transitions"][0]["measurements"],
        "expected_response": {"status": "EXPECTED_RESPONSE_UNAVAILABLE", "baseline_version": None},
        "observed_response": last["measurements"], "residual": None,
        "evidence": [{
            "event_id": item["id"], "prediction": item.get("prediction"), "outcome": item.get("label"),
        } for item in linked],
        "data_quality": last["validity"], "confidence_limitations": last["uncertainty"],
        "epistemic": {
            "FACT": ["Contract and recorded source identifiers"],
            "MEASUREMENT": [item["trigger"] for item in wave["transitions"]],
            "INFERENCE": [],
            "HYPOTHESIS": ["Ordinary price discovery", "Passive replenishment", "Cross-stream timing mismatch",
                           "Persistent execution", "Inventory-sensitive quoting"],
            "UNKNOWN": ["Calibrated reference response", "Participant identity", "Causal mechanism"]
                       + origin.get("missing_evidence", []),
            "REJECTED": last["contradictory_evidence"],
        },
        "falsifiers": [last["falsifier"]], "supersession": [], "timeline": wave["transitions"],
    }


def research_event(event):
    prediction, label = event.get("prediction"), event.get("label")
    return {
        "research_object_id": f"event-{event['id']}", "event_id": event["id"],
        "wave_ids": event.get("wave_ids", []), "source": event["source"],
        "capture_session": event.get("capture_session"), "instrument": event["instrument"],
        "exchange": event["exchange"], "expiry": event.get("expiry"),
        "session": event.get("market_session", "UNKNOWN"), "category": event["category"],
        "observation": event["start"], "source_time_ms": event.get("trade_exchange_ms"),
        "feature_schema": event.get("feature_schema_version"),
        "pre_state": {key: event.get(key) for key in ("pre_mid_ticks", "pre_spread_ticks", "pre_depth")},
        "expected_response": {"status": "EXPECTED_RESPONSE_UNAVAILABLE", "baseline_version": None},
        "observed_response": {"event_metrics": event.get("metrics"), "prediction_window": prediction, "outcome": label},
        "residual": None, "data_quality": event.get("status"),
        "confidence_limitations": "Observed input coverage and anonymous liquidity limit interpretation",
        "epistemic": {"FACT": ["Recorded event identifier and source"], "MEASUREMENT": [event["category"]],
                      "INFERENCE": [], "HYPOTHESIS": event.get("alternatives", []),
                      "UNKNOWN": ["Empirical reference", "Participant identity"] + event.get("missing_evidence", []),
                      "REJECTED": []},
        "falsifiers": ["Check coverage, timestamp alignment, and whether future valid observations retain the move"],
        "supersession": [],
    }
