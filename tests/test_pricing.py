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


def test_sonnet_4_6_carries_the_standard_sonnet_list_price():
    """The incident summarizer's pin (RC1-267). Standard rate, deliberately not
    an introductory one, per the module's own rule."""
    price = pricing.PRICES["claude-sonnet-4-6"]
    assert price.input_per_mtok == Decimal("3.00")
    assert price.output_per_mtok == Decimal("15.00")
