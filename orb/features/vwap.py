"""Session VWAP on the 15-minute series.

Decision (B9): typical-price (HLC3) basis to match TradingView ta.vwap, reset at
09:30 ET each session, accumulating from the 09:30 opening-range bar inclusive.

Causality: VWAP at bar i is a cumulative quantity over bars [session_start, i]
within the same session -- all at or before i. Truncating the input at bar i
therefore never changes the VWAP already computed at bar i. The causality test
suite proves this, and it specifically guards against the classic lookahead bug
of dividing by the whole-day volume total (which would peek at future bars).
"""

from __future__ import annotations

import pandas as pd

from orb import config
from orb.data import session


def typical_price(df: pd.DataFrame) -> pd.Series:
    """HLC3 = (High + Low + Close) / 3 (matches ta.vwap's default source)."""
    return (
        df["High"].astype(float) + df["Low"].astype(float) + df["Close"].astype(float)
    ) / 3.0


def session_vwap(df_15m: pd.DataFrame) -> pd.Series:
    """Cumulative session VWAP, reset each ET session (B9).

    vwap_i = cumsum(hlc3 * volume) / cumsum(volume), accumulated from the first
    bar of the session (the 09:30 OR bar) up to and including bar i.
    """
    session.assert_clean_et_index(df_15m)
    tp = typical_price(df_15m)
    vol = df_15m["Volume"].astype(float)
    pv = tp * vol

    keys = session.session_date(df_15m.index)
    grouped_pv = pv.groupby(keys).cumsum()
    grouped_vol = vol.groupby(keys).cumsum()
    vwap = grouped_pv / grouped_vol
    vwap.index = df_15m.index
    return vwap.rename("session_vwap")
