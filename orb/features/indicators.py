"""ATR(14) and SMA(volume, 20) on the 15-minute series.

Both are strictly causal: the value at bar i depends only on bars <= i. This is
what the causality test suite verifies (recompute on data truncated at bar i
equals the full-data value at bar i).

Parity intent (I15): atr_wilder mirrors TradingView ta.atr (True Range smoothed
with Wilder's RMA); volume_sma mirrors ta.sma(volume, 20)[1] (the 20 bars
strictly prior to the current bar, per B8).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from orb import config


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range on a continuous series (B7).

    TR = max(high-low, |high - prev_close|, |low - prev_close|).
    The very first bar of the whole dataset has no prior close, so its TR is
    high-low. Every later bar -- including each morning's first bar -- uses the
    previous bar's close, so a morning bar's TR includes the overnight gap. This
    is the continuous (not per-session) definition required by B7.
    """
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    prev_close = df["Close"].astype(float).shift(1)

    hl = high - low
    hc = (high - prev_close).abs()
    lc = (low - prev_close).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    tr.iloc[0] = float(high.iloc[0] - low.iloc[0])  # no prior close for the first bar
    return tr.rename("true_range")


def wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's RMA (a.k.a. SMMA), matching TradingView ta.rma.

    Seeded at index (period-1) with the simple mean of the first `period`
    values, then rma[i] = alpha*x[i] + (1-alpha)*rma[i-1] with alpha = 1/period.
    Values before the seed are NaN. Purely recursive and backward-looking, so
    truncating the input never changes an already-computed value.
    """
    if period <= 0:
        raise ValueError("period must be positive.")
    x = series.to_numpy(dtype=float)
    n = x.shape[0]
    out = np.full(n, np.nan, dtype=float)
    if n < period:
        return pd.Series(out, index=series.index, name="rma")

    alpha = 1.0 / period
    out[period - 1] = np.mean(x[:period])
    for i in range(period, n):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return pd.Series(out, index=series.index, name="rma")


def atr(df: pd.DataFrame, period: int = config.ATR_PERIOD) -> pd.Series:
    """ATR(period) = Wilder RMA of True Range on the continuous 15-min series."""
    return wilder_rma(true_range(df), period).rename(f"atr_{period}")


def volume_sma(
    df: pd.DataFrame,
    window: int = config.VOLUME_SMA_WINDOW,
    exclude_current_bar: bool = config.VOLUME_SMA_EXCLUDES_CURRENT_BAR,
) -> pd.Series:
    """Trailing simple moving average of volume on the 15-min series (B8).

    With exclude_current_bar=True (the resolved default) the window covers the
    `window` bars STRICTLY prior to the current bar -- i.e. ta.sma(volume,20)[1]
    -- so the breakout bar's own volume is never in its own baseline. Continuous
    across sessions (B8): the window may span the prior day.
    """
    vol = df["Volume"].astype(float)
    if exclude_current_bar:
        vol = vol.shift(1)
    return vol.rolling(window=window, min_periods=window).mean().rename("volume_sma")
