"""Exhaustive tests for the §5 whole-share position-size formula."""

from __future__ import annotations

import pytest

from orb import config
from orb.strategy.sizing import position_size


def test_normal_hand_computed():
    # equity 5000, entry 100, stop 98 -> psr 2; risk$ 25; raw floor(12.5)=12;
    # cap floor(0.30*5000/100)=15; min=12
    assert position_size(5000.0, 100.0, 98.0) == 12


def test_cap_binds_when_per_share_risk_tiny():
    # psr 0.1 -> raw floor(25/0.1)=250; cap 15; min=15 (cap is the guard)
    assert position_size(5000.0, 100.0, 99.9) == 15


def test_rounds_below_one_returns_zero():
    # psr 50 -> raw floor(25/50)=floor(0.5)=0 -> 0 (skip)
    assert position_size(5000.0, 100.0, 50.0) == 0


def test_exactly_one_share():
    # psr 25 -> raw floor(25/25)=1; cap 15; min=1
    assert position_size(5000.0, 100.0, 75.0) == 1


def test_equity_5000_matches_config():
    assert config.ACCOUNT_EQUITY_GATE == 5000.0
    # entry 100, stop 90 -> psr 10; risk$ 25; raw floor(2.5)=2; cap 15; min=2
    assert position_size(config.ACCOUNT_EQUITY_GATE, 100.0, 90.0) == 2


def test_equity_3000_sidecar():
    assert config.ACCOUNT_EQUITY_SIDECAR == 3000.0
    # equity 3000, entry 100, stop 98 -> psr 2; risk$ 15; raw floor(7.5)=7;
    # cap floor(0.30*3000/100)=9; min=7
    assert position_size(config.ACCOUNT_EQUITY_SIDECAR, 100.0, 98.0) == 7


def test_price_agnostic_live_price_level():
    # entry 78 (SPYM level), stop 76 -> psr 2; risk$ 25; raw 12;
    # cap floor(0.30*5000/78)=floor(19.23)=19; min=12
    assert position_size(5000.0, 78.0, 76.0) == 12


def test_cap_floor_at_high_price():
    # entry 195 (QQQM level), stop 193 -> psr 2; raw 12;
    # cap floor(0.30*5000/195)=floor(7.69)=7; min=7 (cap binds)
    assert position_size(5000.0, 195.0, 193.0) == 7


def test_entry_equal_stop_raises():
    with pytest.raises(ValueError):
        position_size(5000.0, 100.0, 100.0)


def test_entry_below_stop_raises():
    with pytest.raises(ValueError):
        position_size(5000.0, 100.0, 101.0)


def test_returns_int_type():
    assert isinstance(position_size(5000.0, 100.0, 98.0), int)
