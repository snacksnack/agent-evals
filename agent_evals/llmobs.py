"""Datadog LLM Observability, opt-in (RC1-322).

An extra in the same spirit as `sql`: only a consumer that wants traces in
Datadog needs `ddtrace`, and a consumer scoring a narrative offline should
not inherit an APM tracer. `enable()` is a no-op returning False unless both
halves are present — the `llmobs` extra installed and `DD_API_KEY` in the
environment — so every CLI can call it unconditionally and stay runnable on
a machine with neither.

Agentless by design: these are laptop/launchd processes with no local
Datadog agent daemon, and LLM Observability's intake accepts spans directly
with an API key.

**The parse gap.** ddtrace's anthropic integration wraps `Messages.create`
and `Messages.stream` only. `Messages.parse` — the structured-output path
the judge and every launch-planner agent use — issues its own `_post` and
never touches `create` (verified against anthropic 1.2.0 / ddtrace 4.14.0),
so auto-instrumentation alone would silently trace half the estate and drop
the other half. `enable()` therefore patches `parse` with a manual `llm`
span carrying the same model, token and message fields the auto-instrumented
spans get.

**Anthropic-only patching (RC1-331).** `LLMObs.enable()` patches ddtrace's
entire LLM integration list with `raise_errors=True`, so a module-name
collision or version mismatch crashes the billed run tracing was meant to
decorate — launch-planner hit both (its own `agents` layer vs the
openai_agents integration; `mcp>=2` vs the mcp integration's 1.x layout).
The estate is Anthropic-only, so `enable()` env-defaults every other LLM
integration off, and treats any failure to start tracing as a decline
rather than an error: a billed run must never die for its decoration.
"""

from __future__ import annotations

import functools
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from agent_evals.record import CaseResult

try:  # documented optional-dep exception: the `llmobs` extra may be absent
    from ddtrace.llmobs import LLMObs
except ImportError:  # pragma: no cover - exercised only without the extra
    LLMObs = None

_enabled = False
_orig_parse = None


def active() -> bool:
    """Whether `enable()` has run and succeeded in this process."""
    return _enabled


def enable(ml_app: str, *, service: str | None = None) -> bool:
    """Turn on LLM Observability for this process, or quietly decline.

    `ml_app` is the name the fleet shows up under in Datadog's LLM
    Observability view — one per agent (`launch-planner`, `pr-review-agent`,
    `tpm-platform`), not one shared name, so the product's app list *is* the
    fleet inventory. Idempotent; returns whether tracing is on.
    """
    global _enabled
    if _enabled:
        return True
    if LLMObs is None or not os.environ.get("DD_API_KEY"):
        return False
    _restrict_patching_to_anthropic()
    try:
        LLMObs.enable(
            ml_app=ml_app,
            agentless_enabled=True,
            site=os.environ.get("DD_SITE", "datadoghq.com"),
            service=service or ml_app,
        )
        _patch_parse()
    except Exception as exc:
        print(f"llmobs: tracing disabled, enable() failed: {exc}", file=sys.stderr)
        return False
    _enabled = True
    return True


def _llm_integration_modules() -> tuple[str, ...]:
    """The module names `LLMObs.enable()` would patch; empty when unknown.

    Read from ddtrace's own constants — the same two lists its
    `_patch_integrations` concatenates — so the set tracks the installed
    version instead of a hardcoded copy going stale. Private imports,
    guarded: if they move in a future ddtrace, we fall back to patching
    everything, and the try/except in `enable()` still keeps the run alive.
    """
    try:
        from ddtrace.llmobs._constants import SUPPORTED_LLMOBS_INTEGRATIONS
        from ddtrace.llmobs._llmobs import _INTEGRATIONS_W_PROPAGATION_SUPPORT
    except ImportError:  # pragma: no cover - exercised only on a moved layout
        return ()
    modules = set(SUPPORTED_LLMOBS_INTEGRATIONS.values())
    modules |= set(_INTEGRATIONS_W_PROPAGATION_SUPPORT.values())
    return tuple(modules)


def _restrict_patching_to_anthropic() -> None:
    """Env-default every non-anthropic LLM integration off (RC1-331).

    setdefault, not setenv: an explicitly configured `DD_TRACE_<X>_ENABLED`
    in the environment still wins. The env-var id mirrors ddtrace's
    `_integration_env_var_id` (upper-case, hyphens to underscores).
    """
    for module in _llm_integration_modules():
        if module == "anthropic":
            continue
        os.environ.setdefault(f"DD_TRACE_{module.upper().replace('-', '_')}_ENABLED", "false")


def _patch_parse() -> None:
    """Wrap `Messages.parse` in an `llm` span (see the parse gap above)."""
    # Deferred, not top-level: importing this module must not load the LLM
    # SDK. launch-planner's planner-core boundary asserts `anthropic` never
    # enters sys.modules in its credential-free suite, and its evals CLI
    # imports llmobs unconditionally — only enable() may pay this import.
    from anthropic.resources.messages import Messages

    global _orig_parse
    if _orig_parse is not None:
        return
    _orig_parse = Messages.parse

    @functools.wraps(_orig_parse)
    def parse(self, *args, **kwargs):
        model = str(kwargs.get("model", ""))
        with LLMObs.llm(
            model_name=model, model_provider="anthropic", name="anthropic.parse"
        ) as span:
            response = _orig_parse(self, *args, **kwargs)
            output = "".join(
                block.text for block in getattr(response, "content", []) if hasattr(block, "text")
            )
            LLMObs.annotate(
                span,
                input_data=kwargs.get("messages"),
                output_data=[{"role": "assistant", "content": output}],
            )
            usage = getattr(response, "usage", None)
            if usage is not None:
                LLMObs.annotate(
                    span,
                    metrics={
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "total_tokens": usage.input_tokens + usage.output_tokens,
                    },
                )
            return response

    Messages.parse = parse


class _NullCase:
    """The disabled handle: every runner can call `record()` unconditionally."""

    def record(self, result: CaseResult) -> None:
        return None


class _Case:
    def __init__(self, span) -> None:
        self._span = span

    def record(self, result: CaseResult) -> None:
        """Attach the scored outcome to the trace.

        The verdict and cost go on as *evaluations*, not just tags — that is
        the column Datadog renders next to its own built-in evals, which is
        exactly where a harness verdict belongs.
        """
        verdict = "error" if result.error else ("pass" if result.passed else "fail")
        LLMObs.annotate(self._span, tags={"verdict": verdict})
        exported = LLMObs.export_span(self._span)
        if exported is None:
            return
        LLMObs.submit_evaluation(
            label="harness_verdict", metric_type="categorical", value=verdict, span=exported
        )
        LLMObs.submit_evaluation(
            label="cost_usd",
            metric_type="score",
            value=float(result.usage.cost_usd),
            span=exported,
        )


@contextmanager
def case(case_id: str) -> Iterator[_Case | _NullCase]:
    """One eval case as a workflow span; a no-op handle when tracing is off.

    The span wraps subject *and* scoring so the trace shows what a case
    actually costs end to end; `record()` the result before leaving the
    block.
    """
    if not _enabled:
        yield _NullCase()
        return
    with LLMObs.workflow(name=case_id) as span:
        yield _Case(span)
