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


# --- prompt-cache tokens (RC1-392) ------------------------------------------


def test_cache_rates_match_the_published_page_to_the_token():
    """Sonnet 4.6: $3.75/MTok to write a 5-minute cache entry, $0.30/MTok to
    read one. Haiku 4.5: $1.25 and $0.10. Verified against the prompt-caching
    page 2026-09-07; one million tokens of each so the number *is* the page's."""
    mtok = 1_000_000
    assert pricing.cost_usd("claude-sonnet-4-6", 0, 0, cache_creation_input_tokens=mtok) == Decimal(
        "3.75"
    )
    assert pricing.cost_usd("claude-sonnet-4-6", 0, 0, cache_read_input_tokens=mtok) == Decimal(
        "0.30"
    )
    assert pricing.cost_usd("claude-haiku-4-5", 0, 0, cache_creation_input_tokens=mtok) == Decimal(
        "1.25"
    )
    assert pricing.cost_usd("claude-haiku-4-5", 0, 0, cache_read_input_tokens=mtok) == Decimal(
        "0.10"
    )
    assert Decimal("1.25") == pricing.CACHE_WRITE
    assert Decimal("0.1") == pricing.CACHE_READ


def test_a_cached_call_is_not_priced_as_nearly_free():
    """The RC1-392 shape: 8 uncached input tokens, ~10K of context served
    through the cache. Priced on `input_tokens` alone the call looks like
    output-only; priced on all four counts the context is most of the bill."""
    uncached_only = pricing.cost_usd("claude-sonnet-4-6", 8, 1000)
    full = pricing.cost_usd(
        "claude-sonnet-4-6", 8, 1000, cache_creation_input_tokens=4000, cache_read_input_tokens=6000
    )
    # 8 * 3 + 1000 * 15 + 4000 * 3 * 1.25 + 6000 * 3 * 0.1 = 24 + 15000 + 15000 + 1800 µ$
    assert full == Decimal("0.031824")
    assert uncached_only == Decimal("0.015024")
    assert full - uncached_only == Decimal("0.0168"), "the cache tokens are half the bill"


def test_cache_counts_default_to_zero_so_an_uncached_caller_is_unchanged():
    assert pricing.cost_usd("claude-sonnet-4-6", 1000, 2000) == Decimal("0.033")
    assert pricing.cost_usd(
        "claude-sonnet-4-6", 1000, 2000, cache_creation_input_tokens=0, cache_read_input_tokens=0
    ) == Decimal("0.033")
