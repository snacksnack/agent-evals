"""agent-evals — measuring whether an LLM system's output is any good.

Extracted from `launch-planner-agent` once a second consumer made the seams
visible rather than guessed at (ADR-0030, RC1-261). Three repos run LLM systems
in production; this is the part of measuring them that is not specific to any
one of them.

What it carries:

* **`Case`** — a frozen input and the *characteristics* its output must exhibit.
  Never an expected output: these systems are generative, a case pinning an exact
  string fails on a harmless rewording, and a brittle assertion gets deleted
  rather than fixed.
* **`RunRecord`** — append-only results carrying subject version, token cost and
  latency. A score with no version attached cannot be acted on.
* **`groundedness`** — deterministic claim checking. Ticket keys, dates,
  day-counts and contradicted severity, checked against the structured input with
  no model involved. Precision over recall throughout: a checker that flags
  correct output gets muted, and a muted checker catches nothing.
* **`rubric` / `judge` / `agreement` / `construct`** — the calibration
  machinery. A judge earns the right to fail a build by agreeing with a human,
  not by looking plausible.

What it deliberately does not carry: subjects, fixtures, credentials, or
configuration. A subject belongs with the code it tests; the library has no
business knowing a consumer's environment-variable prefix.

**Advisory is a first-class concept.** `CharacteristicResult.advisory` marks a
measurement that is reported but cannot fail a build. Most judge-scored
dimensions live there, and saying so out loud is the difference between a gate
people trust and one they disable.
"""

from __future__ import annotations

__version__ = "0.1.2"

from agent_evals.budget import Ceiling, breaches_for
from agent_evals.case import Case
from agent_evals.record import (
    CaseResult,
    CharacteristicResult,
    DuplicateRunId,
    RunRecord,
    RunStore,
    SubjectVersion,
    Usage,
    new_run_id,
)

__all__ = [
    "Case",
    "CaseResult",
    "Ceiling",
    "CharacteristicResult",
    "DuplicateRunId",
    "RunRecord",
    "RunStore",
    "SubjectVersion",
    "Usage",
    "__version__",
    "breaches_for",
    "new_run_id",
]
