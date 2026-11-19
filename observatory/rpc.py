"""Connection-scoped depth snapshot receipt and ordered publication telemetry."""

from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SnapshotRequest:
    request_id: int
    connection_epoch: int
    instrument: str
    generation: int
    recovery_epoch: int
    created: float
    sent: float | None = None
    deadline: float | None = None
    received: float | None = None
    queued: float | None = None
    dispatched: float | None = None
    committed: float | None = None
    outcomes: list[str] = field(default_factory=list)
    stages: dict[str, float] = field(default_factory=dict)
    pressure_at_send: int = 0
    dispatch_budget: float = 2.0
    publication_budget: float = 2.0

    def mark(self, stage: str, now: float) -> None:
        self.stages.setdefault(stage, now)

    def wire(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id, "connection_epoch": self.connection_epoch,
            "instrument": self.instrument, "generation": self.generation, "recovery_epoch": self.recovery_epoch,
            "time_basis": "same_process_monotonic_offsets_from_creation",
            "receipt_deadline_ms": None if self.deadline is None else (self.deadline - self.created) * 1000,
            "dispatch_budget_ms": self.dispatch_budget * 1000,
            "publication_budget_ms": self.publication_budget * 1000,
            "stages_ms": {stage: (stamp - self.created) * 1000 for stage, stamp in
                          sorted(self.stages.items(), key=lambda pair: pair[1])},
            "outcomes": list(self.outcomes),
        }


class SnapshotRPC:
    def __init__(self):
        self.active: SnapshotRequest | None = None
        self.recent = deque(maxlen=16)
        self.counts = Counter()

    def start(self, request: SnapshotRequest):
        self.active = request
        self.mark("REQUEST_CREATED", request.created)

    def mark(self, stage, now):
        request = self.active
        if request and stage not in request.stages:
            request.mark(stage, now)
            self.counts[stage] += 1

    def finish(self, outcome):
        if self.active:
            self.active.outcomes.append(outcome)
            if outcome not in self.active.stages:
                self.counts[outcome] += 1
            self.recent.append(self.active.wire())
            self.active = None

    def view(self):
        return {"counts": dict(self.counts), "active": self.active.wire() if self.active else None,
                "recent": list(self.recent)}
