"""Tripwire: the frozen strategy parameters must equal their spec values.

This is not a behavioural test -- it is a guard so that an accidental edit to a
frozen parameter (a tuning attempt, a typo) fails loudly in CI instead of
silently changing the strategy (CLAUDE.md rule 1). The literals below are
transcribed from ORB_v1_Phase0_Spec.md §3-§5.
"""

from __future__ import annotations

import datetime as dt

from orb import config


def test_frozen_strategy_parameters_match_spec():
    # §3 session / opening range / decision timeframe
    assert config.RTH_START == dt.time(9, 30)
    assert config.RTH_END == dt.time(16, 0)
    assert config.OR_START == dt.time(9, 30)
    assert config.OR_END == dt.time(9, 45)
    assert config.DECISION_TF_MINUTES == 15

    # §3 buffer / ATR
    assert config.ATR_PERIOD == 14
    assert config.BUFFER_ATR_MULT == 0.10
    assert config.BUFFER_FLOOR_DOLLARS == 0.05

    # §3 entry window / exits / frequency
    assert config.ENTRY_WINDOW_FIRST_CLOSE == dt.time(10, 0)
    assert config.ENTRY_WINDOW_LAST_CLOSE == dt.time(11, 0)
    assert config.PROFIT_TARGET is None
    assert config.TIME_EXIT == dt.time(15, 50)
    assert config.MAX_TRADES_PER_DAY_PER_SYMBOL == 1
    assert config.ALLOW_REENTRY_AFTER_STOP is False

    # §4 filters
    assert config.VOLUME_SMA_WINDOW == 20
    assert config.VOLUME_MULTIPLE == 1.3
    assert config.OR_WIDTH_MIN_PCT == 0.15
    assert config.OR_WIDTH_MAX_PCT == 1.0
    assert config.VIX_MIN == 12.0
    assert config.VIX_MAX == 28.0
    assert config.MACRO_SKIP_EVENT_TYPES == ("FOMC", "CPI", "NFP")

    # §5 sizing
    assert config.RISK_PER_TRADE == 0.005
    assert config.NOTIONAL_CAP_PCT == 0.30


def test_resolved_conventions_are_pinned():
    assert config.SESSION_TZ == "America/New_York"
    assert config.BAR_LABEL == "interval_start"
    assert config.BAR_INTERVAL_CLOSED == "left"
    assert config.EQUITY_MODE == "fixed"
    assert config.OOS_START == dt.date(2024, 1, 1)
    assert config.VWAP_PRICE_BASIS == "hlc3"
    assert config.ATR_METHOD == "wilder"
    assert config.VOLUME_SMA_EXCLUDES_CURRENT_BAR is True
