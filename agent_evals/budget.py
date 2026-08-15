"""Declared ceilings per subject, and what happens when one is crossed.

The `Ceiling` type lives here; **the ceilings themselves live with the
consumer**, because a limit is a claim about a specific subject on a specific
model and the library has no business knowing either.

RC1-254 asks for cost, latency and token budgets with real numbers attached. The
numbers here are **measured, not guessed** — each ceiling is set from an observed
run with headroom, and the observation is recorded next to it so a future reader
can tell a deliberate limit from a number someone liked the look of.

## A breach is a finding, not an exception

It appears in the same report as the quality findings, because the decision it
informs — *can this subject move to a cheaper model* — is answered by looking at
cost and quality together. Splitting them into two reports is how the cost one
stops being read.

## Breaches are advisory

A run that costs more than expected has not produced a wrong answer, and failing
a build on it would be failing on the weather. RC1-255 gates on correctness;
cost is surfaced, tracked, and left for a human. The one thing a budget must not
do is go unnoticed, and `CharacteristicResult.advisory` already carries exactly
that distinction through the run record.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Ceiling:
    """What a subject is expected to cost, and why that number."""

    subject: str
    max_cost_usd: Decimal
    max_latency_ms: float
    note: str

    def breaches(self, cost: Decimal, latency_ms: float) -> list[str]:
        found = []
        if cost > self.max_cost_usd:
            over = (cost / self.max_cost_usd - 1) * 100 if self.max_cost_usd else Decimal(0)
            found.append(f"cost ${cost} exceeds ${self.max_cost_usd} ceiling by {over:.0f}%")
        if latency_ms > self.max_latency_ms:
            found.append(
                f"latency {latency_ms / 1000:.0f}s exceeds "
                f"{self.max_latency_ms / 1000:.0f}s ceiling"
            )
        return found


def breaches_for(ceiling: Ceiling | None, cost: Decimal, latency_ms: float) -> list[str]:
    """Convenience for callers that may have no ceiling declared for a subject."""
    return ceiling.breaches(cost, latency_ms) if ceiling else []
