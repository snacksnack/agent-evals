"""Pricing: exact to the token, and loud about models it does not know."""

from decimal import Decimal

import pytest

from agent_evals import pricing


def test_cost_is_exact_decimal_arithmetic():
    # 1000 input at $3/MTok + 2000 output at $15/MTok = 0.003 + 0.03
    assert pricing.cost_usd("claude-sonnet-4-6", 1000, 2000) == Decimal("0.033")


def test_unknown_model_raises_rather_than_costing_zero():
    with pytest.raises(pricing.UnknownModelPrice, match="no price on file"):
        pricing.cost_usd("claude-nonexistent-9", 1, 1)


def test_opus_4_8_carries_the_standard_opus_list_price():
    """tpm-automation-platform's drift digest calls this model (RC1-269)."""
    price = pricing.PRICES["claude-opus-4-8"]
    assert price.input_per_mtok == Decimal("5.00")
    assert price.output_per_mtok == Decimal("25.00")


def test_a_model_a_repo_merely_references_stays_unpriced():
    """pr_agent references claude-opus-4-6 but no suite calls it (RC1-269).

    Keeping it out of the table is deliberate: a price for a model nothing
    uses would let a future suite silently bill against an unverified entry.
    """
    assert "claude-opus-4-6" not in pricing.PRICES
    with pytest.raises(pricing.UnknownModelPrice):
        pricing.cost_usd("claude-opus-4-6", 1, 1)


def test_sonnet_4_6_carries_the_standard_sonnet_list_price():
    """The incident summarizer's pin (RC1-267). Standard rate, deliberately not
    an introductory one, per the module's own rule."""
    price = pricing.PRICES["claude-sonnet-4-6"]
    assert price.input_per_mtok == Decimal("3.00")
    assert price.output_per_mtok == Decimal("15.00")
