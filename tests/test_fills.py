"""Exhaustive tests for the §6 fill-price math."""

from __future__ import annotations

import pytest

from orb.strategy import fills


# --- slippage_offset (cents -> dollars) ----------------------------------

def test_offset_cents_to_dollars():
    # 2 cents -> $0.02
    assert fills.slippage_offset(2.0) == pytest.approx(0.02)
    # 1 cent -> $0.01
    assert fills.slippage_offset(1.0) == pytest.approx(0.01)


def test_offset_multipliers():
    assert fills.slippage_offset(2.0, 1) == pytest.approx(0.02)
    assert fills.slippage_offset(2.0, 2) == pytest.approx(0.04)
    assert fills.slippage_offset(2.0, 3) == pytest.approx(0.06)


# --- fill_price: direction ------------------------------------------------

def test_buy_fills_higher():
    assert fills.fill_price(100.0, "buy", 2.0) == pytest.approx(100.02)


def test_sell_fills_lower():
    assert fills.fill_price(100.0, "sell", 2.0) == pytest.approx(99.98)


def test_buy_multipliers():
    assert fills.fill_price(100.0, "buy", 2.0, 1) == pytest.approx(100.02)
    assert fills.fill_price(100.0, "buy", 2.0, 2) == pytest.approx(100.04)
    assert fills.fill_price(100.0, "buy", 2.0, 3) == pytest.approx(100.06)


def test_sell_multipliers():
    assert fills.fill_price(100.0, "sell", 2.0, 1) == pytest.approx(99.98)
    assert fills.fill_price(100.0, "sell", 2.0, 2) == pytest.approx(99.96)
    assert fills.fill_price(100.0, "sell", 2.0, 3) == pytest.approx(99.94)


def test_one_cent_on_low_price():
    assert fills.fill_price(50.0, "buy", 1.0) == pytest.approx(50.01)


def test_unknown_side_raises():
    with pytest.raises(ValueError):
        fills.fill_price(100.0, "hold", 2.0)


# --- stop_fill_reference: gap-through picks the worse price ---------------

def test_stop_no_gap_uses_or_low():
    # bar opened at/above OR_low -> stop reference is OR_low
    assert fills.stop_fill_reference(100.0, 100.0) == 100.0
    assert fills.stop_fill_reference(100.0, 100.5) == 100.0


def test_stop_gap_below_uses_worse_open():
    # bar gapped open below OR_low -> fill at the worse (lower) open
    assert fills.stop_fill_reference(100.0, 99.5) == 99.5


def test_stop_then_sell_slippage_combined():
    # gap-through reference, then sell-side slippage applied
    ref = fills.stop_fill_reference(100.0, 99.5)
    assert fills.fill_price(ref, "sell", 2.0) == pytest.approx(99.48)
