"""Exhaustive tests for the breakout trigger and the five §4 filters.

Each filter is tested with a passing case, a failing case, and the EXACT
boundary, asserting the precise operator (strict > vs inclusive >=/<=).
"""

from __future__ import annotations

import datetime as dt

from orb import config
from orb.strategy import signals as sig


# --- entry_buffer ---------------------------------------------------------

def test_entry_buffer_atr_dominates():
    # 0.10 * 2.0 = 0.20 > 0.05 floor
    assert sig.entry_buffer(2.0) == 0.20


def test_entry_buffer_floor_dominates_tiny_atr():
    # 0.10 * 0.1 = 0.01 < 0.05 -> floor wins
    assert sig.entry_buffer(0.1) == config.BUFFER_FLOOR_DOLLARS == 0.05


def test_entry_buffer_exactly_at_floor():
    # 0.10 * 0.5 = 0.05 == floor -> max() returns 0.05
    assert sig.entry_buffer(0.5) == 0.05


def test_entry_buffer_just_above_floor():
    assert sig.entry_buffer(0.5001) > 0.05


# --- breakout_triggered (strict >) ---------------------------------------

def test_breakout_strictly_above():
    assert sig.breakout_triggered(100.21, 100.0, 0.20) is True


def test_breakout_exactly_at_threshold_does_not_trigger():
    # close == or_high + buffer must be False (strict >)
    assert sig.breakout_triggered(100.20, 100.0, 0.20) is False


def test_breakout_below_threshold():
    assert sig.breakout_triggered(100.19, 100.0, 0.20) is False


def test_breakout_uses_buffer_from_entry_buffer():
    buf = sig.entry_buffer(2.0)  # 0.20
    assert sig.breakout_triggered(100.20 + 1e-6, 100.0, buf) is True
    assert sig.breakout_triggered(100.20, 100.0, buf) is False


# --- filter 1: volume (>=) ------------------------------------------------

def test_volume_passes_above():
    assert sig.filter_volume(1500.0, 1000.0) is True


def test_volume_exactly_at_multiple_passes():
    # 1.3 * 1000 = 1300 ; volume == 1300 passes (>=)
    assert sig.filter_volume(1300.0, 1000.0) is True


def test_volume_just_below_fails():
    assert sig.filter_volume(1299.999, 1000.0) is False


# --- filter 2: OR-width (inclusive [0.15%, 1.0%]) -------------------------

def test_or_width_inside_band():
    # width 0.5 on mid 100 -> 0.5%
    assert sig.filter_or_width(100.25, 99.75, 100.0) is True


def test_or_width_exact_min_passes():
    # width 0.375 on mid 250 -> 0.375*100/250 = 0.15% exactly
    assert sig.filter_or_width(250.1875, 249.8125, 250.0) is True


def test_or_width_exact_max_passes():
    # width 1.0 on mid 100 -> 1.0% exactly
    assert sig.filter_or_width(100.5, 99.5, 100.0) is True


def test_or_width_below_min_fails():
    # width 0.10 on mid 100 -> 0.10% < 0.15%
    assert sig.filter_or_width(100.05, 99.95, 100.0) is False


def test_or_width_above_max_fails():
    # width 1.5 on mid 100 -> 1.5% > 1.0%
    assert sig.filter_or_width(100.75, 99.25, 100.0) is False


def test_or_width_just_below_min_fails():
    # width 0.375 on mid 250.5 -> 0.1497% < 0.15%
    assert sig.filter_or_width(250.1875, 249.8125, 250.5) is False


def test_or_width_just_above_max_fails():
    # width 1.0 on mid 99.5 -> 1.005% > 1.0%
    assert sig.filter_or_width(100.5, 99.5, 99.5) is False


# --- filter 3: VWAP (strict >) -------------------------------------------

def test_vwap_above_passes():
    assert sig.filter_vwap(100.01, 100.0) is True


def test_vwap_equal_fails():
    assert sig.filter_vwap(100.0, 100.0) is False


def test_vwap_below_fails():
    assert sig.filter_vwap(99.99, 100.0) is False


# --- filter 4: VIX (inclusive [12, 28]) ----------------------------------

def test_vix_inside_passes():
    assert sig.filter_vix(20.0) is True


def test_vix_at_min_passes():
    assert sig.filter_vix(12.0) is True


def test_vix_at_max_passes():
    assert sig.filter_vix(28.0) is True


def test_vix_just_below_min_fails():
    assert sig.filter_vix(11.99) is False


def test_vix_just_above_max_fails():
    assert sig.filter_vix(28.01) is False


# --- filter 5: macro skip -------------------------------------------------

_SKIP = {dt.date(2024, 6, 12), dt.date(2020, 3, 3)}


def test_macro_skip_day_fails():
    assert sig.filter_macro(dt.date(2024, 6, 12), _SKIP) is False


def test_macro_normal_day_passes():
    assert sig.filter_macro(dt.date(2024, 6, 13), _SKIP) is True


# --- composer: evaluate_session ------------------------------------------

def _session_kwargs(**over):
    base = dict(
        or_high=100.5, or_low=99.5, or_mid=100.0,   # 1.0% width -> ok
        prior_day_vix=20.0,                          # ok
        session_date=dt.date(2024, 6, 13),           # not a skip day -> ok
        skip_dates=_SKIP,
    )
    base.update(over)
    return base


def test_session_all_pass():
    g = sig.evaluate_session(**_session_kwargs())
    assert (g.or_width_ok, g.vix_ok, g.macro_ok, g.day_eligible) == (True, True, True, True)


def test_session_vix_out_rules_day_out():
    g = sig.evaluate_session(**_session_kwargs(prior_day_vix=30.0))
    assert g.vix_ok is False and g.day_eligible is False
    assert g.or_width_ok is True and g.macro_ok is True  # others still reported


def test_session_macro_skip_rules_day_out():
    g = sig.evaluate_session(**_session_kwargs(session_date=dt.date(2020, 3, 3)))
    assert g.macro_ok is False and g.day_eligible is False


# --- composer: evaluate_entry --------------------------------------------

def _entry_kwargs(**over):
    base = dict(
        close=101.0,         # well above OR_high + buffer and above VWAP
        or_high=100.0, or_low=99.5, or_mid=100.0,   # width 0.5% -> ok
        atr=2.0,             # buffer 0.20
        volume=2000.0, volume_sma=1000.0,           # 2.0x >= 1.3x -> ok
        vwap=100.5,          # close 101 > vwap -> ok
        prior_day_vix=20.0,  # ok
        session_date=dt.date(2024, 6, 13),
        skip_dates=_SKIP,
    )
    base.update(over)
    return base


def test_entry_all_pass_enters():
    d = sig.evaluate_entry(**_entry_kwargs())
    assert d.enter is True
    assert all([d.breakout, d.volume_ok, d.or_width_ok, d.vwap_ok,
                d.vix_ok, d.macro_ok, d.session_ok])


def test_entry_no_breakout_blocks_but_filters_pass():
    # close exactly at OR_high + buffer (100 + 0.20) -> no breakout, but filters ok
    d = sig.evaluate_entry(**_entry_kwargs(close=100.20, vwap=100.0))
    assert d.breakout is False
    assert d.enter is False
    assert d.volume_ok and d.or_width_ok and d.vix_ok and d.macro_ok and d.session_ok


def test_entry_multiple_filters_fail_each_flag_correct():
    # volume too low AND vix out of band AND vwap equal (fails); breakout ok
    d = sig.evaluate_entry(**_entry_kwargs(
        volume=1000.0, volume_sma=1000.0,  # 1.0x < 1.3x -> volume fails
        prior_day_vix=35.0,                # vix fails
        vwap=101.0,                        # close 101 == vwap -> vwap fails (strict >)
    ))
    assert d.breakout is True
    assert d.volume_ok is False
    assert d.vix_ok is False
    assert d.vwap_ok is False
    assert d.or_width_ok is True and d.macro_ok is True
    assert d.session_ok is False  # vix failed
    assert d.enter is False


def test_entry_session_fail_still_reports_bar_level():
    # macro skip day: session_ok False, enter False, but bar-level flags computed
    d = sig.evaluate_entry(**_entry_kwargs(session_date=dt.date(2020, 3, 3)))
    assert d.macro_ok is False
    assert d.session_ok is False
    assert d.enter is False
    assert d.breakout is True and d.volume_ok is True and d.vwap_ok is True
