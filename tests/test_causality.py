"""The load-bearing test: every feature is strictly causal (no lookahead).

For each feature we assert the recompute-on-truncated-data property:

    feature(df.iloc[:k]).iloc[-1] == feature(df).iloc[k-1]   for every k

i.e. the value computed at bar i never changes when bars after i are removed.
A feature that fails this is peeking at the future -- exactly the failure
CLAUDE.md rule 5 forbids, and the kind that produces an unrealistically good
backtest. A green run here is what earns the right to trust a single backtest
number downstream.

The suite also includes "teeth" tests proving the check actually CATCHES known
lookahead (a forward shift, and a whole-day-total VWAP), so a passing feature
test means something.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from orb.features import indicators, vwap


def _equal(a, b) -> bool:
    a_na, b_na = pd.isna(a), pd.isna(b)
    if a_na or b_na:
        return bool(a_na and b_na)
    return bool(np.isclose(float(a), float(b), rtol=0, atol=1e-12))


def first_lookahead_index(feature_fn, df: pd.DataFrame, start: int = 1):
    """Return the first bar index that changes under truncation, or None.

    None means the feature is causal across the whole series.
    """
    full = feature_fn(df)
    for k in range(start, len(df) + 1):
        partial = feature_fn(df.iloc[:k])
        if not _equal(partial.iloc[-1], full.iloc[k - 1]):
            return k - 1
    return None


# --------------------------------------------------------------------------
# The real features must be causal.
# --------------------------------------------------------------------------

def test_atr_is_causal(bars_15m):
    assert first_lookahead_index(indicators.atr, bars_15m) is None


def test_true_range_is_causal(bars_15m):
    assert first_lookahead_index(indicators.true_range, bars_15m) is None


def test_volume_sma_is_causal(bars_15m):
    assert first_lookahead_index(indicators.volume_sma, bars_15m) is None


def test_session_vwap_is_causal(bars_15m):
    assert first_lookahead_index(vwap.session_vwap, bars_15m) is None


def test_typical_price_is_causal(bars_15m):
    assert first_lookahead_index(vwap.typical_price, bars_15m) is None


# --------------------------------------------------------------------------
# Teeth: the check must catch deliberate lookahead, or it proves nothing.
# --------------------------------------------------------------------------

def test_check_catches_forward_shift(bars_15m):
    """A feature that reads the NEXT bar's close must be flagged."""
    leaky = lambda df: df["Close"].shift(-1)
    assert first_lookahead_index(leaky, bars_15m) is not None


def test_check_catches_whole_day_total_vwap(bars_15m):
    """A VWAP divided by the whole-session volume TOTAL peeks at future bars."""

    def leaky_vwap(df):
        from orb.data import session as sess

        tp = vwap.typical_price(df)
        vol = df["Volume"].astype(float)
        keys = sess.session_date(df.index)
        pv_total = (tp * vol).groupby(keys).transform("sum")
        vol_total = vol.groupby(keys).transform("sum")
        out = pv_total / vol_total
        out.index = df.index
        return out

    assert first_lookahead_index(leaky_vwap, bars_15m) is not None
