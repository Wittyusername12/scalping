"""1-minute -> 15-minute aggregation: anchoring, labeling, and OHLCV correctness.

Verifies the B1 conventions the whole strategy depends on: bars are anchored at
09:30, labeled by interval start, half-open [start, end); the first bar of a
session is the 09:30-09:45 opening range.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from orb import config
from orb.data import session


def test_first_bar_per_session_is_the_opening_range(bars_15m):
    """Each session's first 15-min bar is labeled 09:30 (the OR window)."""
    keys = session.session_date(bars_15m.index)
    for _, day in bars_15m.groupby(keys):
        assert day.index[0].time() == dt.time(9, 30)
        # second bar (first decision bar) is labeled 09:45, evaluated at 10:00 close
        assert day.index[1].time() == dt.time(9, 45)


def test_bars_are_15min_and_within_rth(bars_15m):
    labels = bars_15m.index.time
    assert all(t >= config.RTH_START for t in labels)
    assert all(t < config.RTH_END for t in labels)
    # 26 fifteen-minute bars per full session (09:30 .. 15:45)
    keys = session.session_date(bars_15m.index)
    counts = bars_15m.groupby(keys).size()
    assert (counts == 26).all()
    assert bars_15m.index[-1].time() == dt.time(15, 45)


def test_ohlcv_matches_underlying_minutes(min_bars, bars_15m):
    """Reconstruct one 15-min bar by hand from its [start, end) minutes."""
    et = config.SESSION_TZ
    start = pd.Timestamp("2026-06-23 10:00", tz=et)  # a 09:45-? no: 10:00 bar start
    end = start + pd.Timedelta(minutes=config.DECISION_TF_MINUTES)
    window = min_bars.loc[(min_bars.index >= start) & (min_bars.index < end)]
    bar = bars_15m.loc[start]

    assert bar["Open"] == window["Open"].iloc[0]
    assert bar["Close"] == window["Close"].iloc[-1]
    assert bar["High"] == window["High"].max()
    assert bar["Low"] == window["Low"].min()
    assert bar["Volume"] == window["Volume"].sum()


def test_aggregation_is_causal_per_bar(min_bars):
    """A 15-min bar uses ONLY minutes inside its own [start, end) window.

    Truncating the 1-minute feed right after a bar's window closes must yield an
    identical 15-minute bar -- i.e. it never borrowed a future minute.
    """
    from orb.data import aggregate

    full = aggregate.resample_15m(min_bars)
    et = config.SESSION_TZ
    start = pd.Timestamp("2026-06-23 11:15", tz=et)
    end = start + pd.Timedelta(minutes=config.DECISION_TF_MINUTES)

    truncated_min = min_bars.loc[min_bars.index < end]
    truncated = aggregate.resample_15m(truncated_min)

    pd.testing.assert_series_equal(truncated.loc[start], full.loc[start])
