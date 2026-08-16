"""Tests for the kobo conversion helpers.

These pin down the two things ``money.py`` exists to guarantee: conversion does
not lose a kobo, and arithmetic done in kobo does not drift the way the
equivalent float arithmetic does.
"""

from app.money import to_kobo, to_naira


def test_round_trip_preserves_amount():
    for naira in [0.0, 1.15, 20.05, 8.35, 37333.33, 56000.0, 112000.0]:
        assert to_naira(to_kobo(naira)) == naira


def test_to_kobo_rounds_rather_than_truncates():
    """``int(1.15 * 100)`` is 114 — the float lands just below 115.

    Truncating would silently drop a kobo on every payment that hits this
    class of value, so the conversion must round.
    """
    assert 1.15 * 100 < 115  # the trap this guards against
    assert to_kobo(1.15) == 115
    assert to_kobo(37333.33) == 3733333


def test_kobo_arithmetic_does_not_drift_where_float_does():
    """The reason this module exists.

    These three partials sum to the total exactly in decimal, but not in
    binary floating point. In kobo the books balance; in naira they do not.
    """
    total_repayable = 48702.38
    partials = [3622.74, 26794.56, 18285.08]

    float_paid = 0.0
    for amount in partials:
        float_paid += amount
    assert total_repayable - float_paid != 0.0  # drifts

    kobo_paid = 0
    for amount in partials:
        kobo_paid += to_kobo(amount)
    assert to_kobo(total_repayable) - kobo_paid == 0  # exact
