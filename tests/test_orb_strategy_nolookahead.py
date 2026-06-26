"""No-lookahead verification for the assembled ORBStrategy.

Two layers, on committed synthetic fixtures with known answers (no real data):

1. Point-in-time replay: feed 1-min bars only up to bar k, record the action at
   k; compare to the full-day run's action at k, for every k. Any future bar that
   changes a past decision is a failure. Covers entry, stop, and flatten. A
   deliberately-leaky variant (a feature shifted one 1-min bar early) must be
   CAUGHT by the same replay (teeth).

2. Hand-traced fixture days asserting exact bar and price for: clean breakout,
   OR-width rejection, stop at OR_low, gap-below stop, winner held to 15:50, and
   breakouts outside the (09:45, 11:00] window.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from orb import config
from orb.strategy.orb_strategy import (
    Action,
    actions_by_ts,
    build_execution_frame,
    run_strategy,
)

ET = config.SESSION_TZ
DAY = "2026-06-22"            # a Monday; not on the macro-skip list
DAY_DATE = dt.date(2026, 6, 22)
VIX = {DAY_DATE: 20.0}        # inside [12, 28]

# OR within the width band: width 0.5 on mid 100 -> 0.5%
OR_HIGH, OR_LOW = 100.25, 99.75
# atr 2.0 -> buffer 0.20 -> breakout threshold = 100.45
BREAKOUT_CLOSE = 100.60      # > 100.45 ; fill (buy, 2c) = 100.62
EXPECTED_SHARES = 14         # position_size(5000, 100.62, 99.75) = min(28, 14)


# --------------------------------------------------------------------------
# fixture builders
# --------------------------------------------------------------------------

def _mins(start="09:30", end="15:59", day=DAY):
    return pd.date_range(f"{day} {start}", f"{day} {end}", freq="1min", tz=ET)


def benign_bars(index, price=100.5):
    """Flat 1-min bars O=H=L=C (low never breaches a stop below `price`)."""
    return pd.DataFrame(
        {"Open": price, "High": price, "Low": price, "Close": price, "Volume": 1000.0},
        index=index,
    )


def base_features(day=DAY, or_high=OR_HIGH, or_low=OR_LOW,
                  atr=2.0, vwap=100.0, vol_sma=1000.0, volume15=2000.0,
                  close15=100.0):
    """15-min features indexed by START; OR constant per session; decision rows
    default to NON-breakout (close15 below threshold)."""
    starts = pd.date_range(f"{day} 09:30", f"{day} 15:45", freq="15min", tz=ET)
    df = pd.DataFrame(index=starts)
    df["or_high"] = or_high
    df["or_low"] = or_low
    df["or_mid"] = (or_high + or_low) / 2.0
    df["atr"] = atr
    df["vwap"] = vwap
    df["vol_sma"] = vol_sma
    df["volume15"] = volume15
    df["close15"] = close15
    return df


def set_breakout(features, decision_hhmm, close15=BREAKOUT_CLOSE,
                 volume15=2000.0, vwap=100.0, atr=2.0):
    """Make the 15-min bar whose CLOSE == decision_hhmm a qualifying breakout.

    Decision at close T reads the 15-min row starting at T-15min.
    """
    start = pd.Timestamp(f"{DAY} {decision_hhmm}", tz=ET) - pd.Timedelta(minutes=15)
    features.loc[start, ["close15", "volume15", "vwap", "atr"]] = [close15, volume15, vwap, atr]
    return features


def set_bar(min_bars, hhmm, o, h, l, c):
    ts = pd.Timestamp(f"{DAY} {hhmm}", tz=ET)
    min_bars.loc[ts, ["Open", "High", "Low", "Close"]] = [o, h, l, c]


# --------------------------------------------------------------------------
# 1. Point-in-time replay
# --------------------------------------------------------------------------

def replay_mismatches(min_bars, features, vix, skip=frozenset(), leak=None):
    """Return [(ts, incremental_action, full_action)] where feeding bars only up
    to k changes the action at k vs the full run. Empty == no lookahead."""
    full = run_strategy(build_execution_frame(min_bars, features, vix, leak_feature=leak),
                        skip_dates=skip)
    full_map = actions_by_ts(full)

    out = []
    n = len(min_bars)
    for k in range(1, n):  # bar 0 is skipped by the engine; Backtest needs >= 2 rows
        sub = min_bars.iloc[:k + 1]
        inc = run_strategy(build_execution_frame(sub, features, vix, leak_feature=leak),
                           skip_dates=skip)
        if not inc.records:
            continue
        ts_k, acts_k = inc.records[-1]
        if ts_k != min_bars.index[k]:
            continue
        if acts_k != full_map.get(ts_k):
            out.append((ts_k, acts_k, full_map.get(ts_k)))
    return out


def _entry_stop_fixture():
    """Compact contiguous day: breakout at 10:15, stop at 10:20."""
    idx = _mins(end="10:30")
    bars = benign_bars(idx)
    set_bar(bars, "10:20", o=100.0, h=100.0, l=99.50, c=100.0)  # low <= OR_low -> stop
    feats = set_breakout(base_features(), "10:15")
    return bars, feats


def _flatten_fixture():
    """Compact non-contiguous day: breakout at 10:15, no stop, flatten at 15:50."""
    idx = _mins(end="10:20").append(_mins(start="15:45", end="15:52"))
    bars = benign_bars(idx)
    set_bar(bars, "15:50", o=101.50, h=101.50, l=101.50, c=101.50)
    feats = set_breakout(base_features(), "10:15")
    return bars, feats


def test_replay_entry_and_stop_have_no_lookahead():
    bars, feats = _entry_stop_fixture()
    assert replay_mismatches(bars, feats, VIX) == []


def test_replay_flatten_has_no_lookahead():
    bars, feats = _flatten_fixture()
    assert replay_mismatches(bars, feats, VIX) == []


def test_replay_catches_injected_lookahead():
    """Teeth: shifting close15 one 1-min bar early must be caught by the replay."""
    bars, feats = _entry_stop_fixture()
    leaked = replay_mismatches(bars, feats, VIX, leak="close15")
    assert len(leaked) > 0, "replay failed to catch injected lookahead"
    # the leak makes the entry bar's value unavailable under truncation
    assert any(ts.strftime("%H:%M") == "10:15" for ts, _, _ in leaked)


# --------------------------------------------------------------------------
# 2. Hand-traced fixtures (exact bar + price)
# --------------------------------------------------------------------------

def _run(min_bars, features, skip=frozenset()):
    return run_strategy(build_execution_frame(min_bars, features, VIX), skip_dates=skip)


def test_clean_breakout_fires_at_bar_and_fills_close_plus_slippage():
    bars = benign_bars(_mins())
    feats = set_breakout(base_features(), "10:15")
    strat = _run(bars, feats)

    enters = [(ts, a) for ts, acts in strat.records for a in acts if a.kind == "enter"]
    assert len(enters) == 1
    ts, a = enters[0]
    assert ts == pd.Timestamp(f"{DAY} 10:15", tz=ET)         # the decision bar
    assert a.price == pytest.approx(100.62)                  # close15 100.60 + 2c buy
    assert a.shares == EXPECTED_SHARES
    # no entry action before the window opens
    assert all(ts >= pd.Timestamp(f"{DAY} 10:00", tz=ET)
               for ts, acts in strat.records for a in acts if a.kind in ("enter", "size_skip"))


def test_or_width_rejection_means_no_trade():
    # OR width 3.0 on mid 100.5 -> ~2.99% > 1.0% -> session ineligible
    feats = base_features(or_high=102.0, or_low=99.0)
    set_breakout(feats, "10:15", close15=103.0)              # would-be breakout, but day is out
    bars = benign_bars(_mins())
    strat = _run(bars, feats)
    assert strat.round_trips == []
    assert all(a.kind != "enter" for _, acts in strat.records for a in acts)


def test_stop_out_fills_at_or_low():
    bars = benign_bars(_mins(end="10:30"))
    set_bar(bars, "10:20", o=100.0, h=100.0, l=99.50, c=100.0)   # traded down through OR_low
    feats = set_breakout(base_features(), "10:15")
    strat = _run(bars, feats)
    assert len(strat.round_trips) == 1
    tr = strat.round_trips[0]
    assert tr.exit_kind == "stop"
    assert tr.exit_ts == pd.Timestamp(f"{DAY} 10:20", tz=ET)
    assert tr.exit_price == pytest.approx(99.73)             # OR_low 99.75 - 2c sell
    assert tr.shares == EXPECTED_SHARES


def test_stop_gap_below_or_low_fills_worse_at_open():
    bars = benign_bars(_mins(end="10:30"))
    set_bar(bars, "10:20", o=99.00, h=99.00, l=98.50, c=99.00)   # gapped open below OR_low
    feats = set_breakout(base_features(), "10:15")
    strat = _run(bars, feats)
    tr = strat.round_trips[0]
    assert tr.exit_kind == "stop"
    assert tr.exit_price == pytest.approx(98.98)             # open 99.00 - 2c (worse than OR_low)


def test_winner_held_to_1550_flatten():
    bars = benign_bars(_mins())                              # benign all day -> never hits stop
    set_bar(bars, "15:50", o=101.50, h=101.50, l=101.50, c=101.50)
    feats = set_breakout(base_features(), "10:15")
    strat = _run(bars, feats)
    assert len(strat.round_trips) == 1
    tr = strat.round_trips[0]
    assert tr.exit_kind == "flatten"
    assert tr.exit_ts == pd.Timestamp(f"{DAY} 15:50", tz=ET)
    assert tr.exit_price == pytest.approx(101.48)            # 15:50 close 101.50 - 2c sell


def test_breakout_after_1100_does_not_trade():
    feats = base_features()
    set_breakout(feats, "11:15", close15=103.0)              # decision 11:15 is outside the window
    bars = benign_bars(_mins())
    strat = _run(bars, feats)
    assert strat.round_trips == []
    assert all(a.kind != "enter" for _, acts in strat.records for a in acts)


def test_breakout_exactly_at_1100_trades():
    # 11:00 is the last eligible decision close -> inclusive upper bound
    feats = set_breakout(base_features(), "11:00")
    bars = benign_bars(_mins())
    strat = _run(bars, feats)
    enters = [ts for ts, acts in strat.records for a in acts if a.kind == "enter"]
    assert enters == [pd.Timestamp(f"{DAY} 11:00", tz=ET)]


def test_breakout_at_first_decision_1000_trades():
    # 10:00 is the first eligible decision close -> inclusive lower bound
    feats = set_breakout(base_features(), "10:00")
    bars = benign_bars(_mins())
    strat = _run(bars, feats)
    enters = [ts for ts, acts in strat.records for a in acts if a.kind == "enter"]
    assert enters == [pd.Timestamp(f"{DAY} 10:00", tz=ET)]


def test_macro_skip_day_no_trade():
    feats = set_breakout(base_features(), "10:15")
    bars = benign_bars(_mins())
    strat = _run(bars, feats, skip={DAY_DATE})              # whole session skipped
    assert strat.round_trips == []
    assert all(a.kind != "enter" for _, acts in strat.records for a in acts)
