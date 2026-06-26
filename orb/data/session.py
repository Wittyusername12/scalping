"""Timezone localization and per-session helpers.

backtesting.py has no concept of a trading session (B12): it treats a
concatenated multi-day intraday index as one continuous series. So every
per-day notion -- the opening range, the VWAP reset, the daily trade cap --
must be derived explicitly here, keyed on the ET session date.

These helpers are pure functions over a pandas DatetimeIndex / DataFrame; they
do not mutate their inputs.
"""

from __future__ import annotations

import pandas as pd

from orb import config


def to_et(df: pd.DataFrame, assume_tz: str | None = None) -> pd.DataFrame:
    """Return a copy of *df* with its DatetimeIndex in America/New_York (B1).

    - If the index is tz-aware, it is converted to ET.
    - If it is tz-naive, *assume_tz* must be given (the source timezone, e.g.
      'UTC' for Alpaca); the index is localized to that tz then converted to ET.

    We never silently assume a fixed UTC offset -- conversion is DST-aware.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("Expected a DatetimeIndex.")

    out = df.copy()
    idx = out.index
    if idx.tz is None:
        if assume_tz is None:
            raise ValueError(
                "Index is tz-naive; pass assume_tz (the source timezone) so we "
                "can localize before converting to ET (B1)."
            )
        idx = idx.tz_localize(assume_tz)
    out.index = idx.tz_convert(config.SESSION_TZ)
    return out


def assert_clean_et_index(df: pd.DataFrame) -> None:
    """Validate the contract every downstream module relies on."""
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise TypeError("Expected a DatetimeIndex.")
    if idx.tz is None:
        raise ValueError("Index must be tz-aware (localized to ET via to_et).")
    if str(idx.tz) != config.SESSION_TZ:
        raise ValueError(f"Index tz must be {config.SESSION_TZ}, got {idx.tz}.")
    if not idx.is_monotonic_increasing:
        raise ValueError("Index must be sorted ascending.")
    if idx.has_duplicates:
        raise ValueError("Index has duplicate timestamps.")


def session_date(index: pd.DatetimeIndex) -> pd.Index:
    """The ET calendar date of each timestamp (the session it belongs to).

    Used for grouping, OR computation, VWAP reset, the daily trade cap, and
    macro/OOS date matching. Requires an ET-localized index.
    """
    if index.tz is None:
        raise ValueError("session_date requires a tz-aware (ET) index.")
    return pd.Index(index.normalize().date, name="session_date")


def rth_mask(index: pd.DatetimeIndex) -> pd.Series:
    """Boolean mask for regular-hours bars: time in [09:30, 16:00) ET (§3)."""
    t = index.tz_convert(config.SESSION_TZ).time if index.tz else index.time
    times = pd.Series([ts.time() for ts in index], index=index)
    return (times >= config.RTH_START) & (times < config.RTH_END)


def to_rth(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to regular-hours bars only (no pre/post-market)."""
    assert_clean_et_index(df)
    return df.loc[rth_mask(df.index).to_numpy()]
