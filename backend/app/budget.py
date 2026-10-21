from dataclasses import dataclass, field
from threading import Lock

LIMITS = {
    "quick": {
        "tasks": 2,
        "searches": 6,
        "fetches": 8,
        "models": 24,
        "followups": 1,
        "seconds": 180,
    },
    "standard": {
        "tasks": 3,
        "searches": 12,
        "fetches": 20,
        "models": 36,
        "followups": 2,
        "seconds": 600,
    },
}


class BudgetExhausted(RuntimeError):
    """Expected bounded-work stop, distinct from provider failure."""

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(f"{kind} budget exhausted")


@dataclass
class RunBudget:
    depth: str
    counts: dict[str, int] = field(
        default_factory=lambda: {"searches": 0, "fetches": 0, "models": 0, "cached_calls": 0}
    )
    ended_by_limit: str | None = None
    _lock: Lock = field(default_factory=Lock, repr=False)

    def reserve(self, kind: str, amount: int = 1, *, critical: bool = False) -> bool:
        with self._lock:
            limit = LIMITS[self.depth][kind]
            protected = 5 if kind == "models" and not critical else 0
            if self.counts[kind] + amount > limit - protected:
                self.ended_by_limit = kind
                return False
            self.counts[kind] += amount
            return True

    def require(self, kind: str, *, critical: bool = False):
        if not self.reserve(kind, critical=critical):
            raise BudgetExhausted(kind)

    @property
    def limits(self) -> dict[str, int]:
        return LIMITS[self.depth]
