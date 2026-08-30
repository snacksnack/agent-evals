"""llmobs must be safe to call everywhere: a runner on a machine with no
DD_API_KEY (CI, a contributor laptop) goes through the exact same code path
as an instrumented one, so the disabled paths are the contract under test.
The enabled path is tested against a fake LLMObs — these tests must not
depend on the `llmobs` extra being installed, let alone on an intake."""


import os
from decimal import Decimal

import pytest
from anthropic.resources.messages import Messages

from agent_evals import llmobs
from agent_evals.record import CaseResult, Usage


@pytest.fixture(autouse=True)
def reset_llmobs(monkeypatch):
    yield
    if llmobs._orig_parse is not None:
        Messages.parse = llmobs._orig_parse
        llmobs._orig_parse = None
    llmobs._enabled = False


def _result(*, error: str | None = None) -> CaseResult:
    return CaseResult(
        case_id="c1",
        usage=Usage(input_tokens=10, output_tokens=5, cost_usd=Decimal("0.01"), latency_ms=100.0),
        error=error,
    )


class FakeSpan:
    pass


class FakeLLMObs:
    def __init__(self):
        self.enabled_with = None
        self.annotations = []
        self.evaluations = []

    def enable(self, **kwargs):
        self.enabled_with = kwargs

    def workflow(self, name):
        from contextlib import contextmanager

        @contextmanager
        def cm():
            yield FakeSpan()

        return cm()

    llm = workflow

    def annotate(self, span, **kwargs):
        self.annotations.append(kwargs)

    def export_span(self, span):
        return {"span_id": "1", "trace_id": "2"}

    def submit_evaluation(self, **kwargs):
        self.evaluations.append(kwargs)


def test_enable_declines_without_api_key(monkeypatch):
    monkeypatch.delenv("DD_API_KEY", raising=False)
    monkeypatch.setattr(llmobs, "LLMObs", FakeLLMObs())
    assert llmobs.enable("test-app") is False
    assert llmobs.active() is False


def test_enable_declines_without_ddtrace(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "k")
    monkeypatch.setattr(llmobs, "LLMObs", None)
    assert llmobs.enable("test-app") is False


def test_case_is_noop_when_disabled():
    with llmobs.case("c1") as handle:
        handle.record(_result())  # must not raise


def test_enable_declines_instead_of_raising_when_patching_crashes(monkeypatch, capsys):
    """RC1-331: launch-planner's own `agents` package crashed LLMObs.enable().

    Tracing is decoration — a billed run must never die for it.
    """
    monkeypatch.setenv("DD_API_KEY", "k")

    class CrashingLLMObs(FakeLLMObs):
        def enable(self, **kwargs):
            raise ModuleNotFoundError("No module named 'agents.tracing'")

    monkeypatch.setattr(llmobs, "LLMObs", CrashingLLMObs())
    assert llmobs.enable("test-app") is False
    assert llmobs.active() is False
    assert "agents.tracing" in capsys.readouterr().err


def test_non_anthropic_integrations_are_defaulted_off(monkeypatch):
    """RC1-331: the estate is Anthropic-only; nothing else gets patched."""
    monkeypatch.setenv("DD_API_KEY", "k")
    monkeypatch.setattr(
        llmobs, "_llm_integration_modules", lambda: ("anthropic", "openai_agents", "google-genai")
    )
    monkeypatch.delenv("DD_TRACE_ANTHROPIC_ENABLED", raising=False)
    monkeypatch.delenv("DD_TRACE_OPENAI_AGENTS_ENABLED", raising=False)
    # An explicit environment setting must survive the defaulting.
    monkeypatch.setenv("DD_TRACE_GOOGLE_GENAI_ENABLED", "true")
    monkeypatch.setattr(llmobs, "LLMObs", FakeLLMObs())

    assert llmobs.enable("test-app") is True
    assert "DD_TRACE_ANTHROPIC_ENABLED" not in os.environ
    assert os.environ["DD_TRACE_OPENAI_AGENTS_ENABLED"] == "false"
    assert os.environ["DD_TRACE_GOOGLE_GENAI_ENABLED"] == "true"


def test_enable_patches_parse_and_is_idempotent(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "k")
    fake = FakeLLMObs()
    monkeypatch.setattr(llmobs, "LLMObs", fake)
    original = Messages.parse
    assert llmobs.enable("test-app") is True
    assert Messages.parse is not original
    patched = Messages.parse
    assert llmobs.enable("test-app") is True
    assert Messages.parse is patched
    assert fake.enabled_with["ml_app"] == "test-app"
    assert fake.enabled_with["agentless_enabled"] is True


def test_case_records_verdict_and_cost_as_evaluations(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "k")
    fake = FakeLLMObs()
    monkeypatch.setattr(llmobs, "LLMObs", fake)
    llmobs.enable("test-app")
    with llmobs.case("c1") as handle:
        handle.record(_result())
    by_label = {e["label"]: e for e in fake.evaluations}
    assert by_label["harness_verdict"]["value"] == "pass"
    assert by_label["harness_verdict"]["metric_type"] == "categorical"
    assert by_label["cost_usd"]["value"] == pytest.approx(0.01)
    assert by_label["cost_usd"]["metric_type"] == "score"


def test_case_records_error_verdict(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "k")
    fake = FakeLLMObs()
    monkeypatch.setattr(llmobs, "LLMObs", fake)
    llmobs.enable("test-app")
    with llmobs.case("c1") as handle:
        handle.record(_result(error="boom"))
    verdicts = [e for e in fake.evaluations if e["label"] == "harness_verdict"]
    assert verdicts[0]["value"] == "error"
