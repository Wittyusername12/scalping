"""Aggregate 1-minute bars into the canonical 9:30-anchored 15-minute series.

Decision (I5): source 1-minute bars and resample to 15-minute bars anchored at
09:30, then compute ALL indicators on that single series, so results are
identical regardless of whether the raw feed was 1-min or native 15-min.

Convention (B1): a 15-minute bar's timestamp marks its START, bins are
half-open [start, end), so the first bar of a session is the 09:30-09:45
opening range, labeled 09:30; the first decision bar is 09:45-10:00, labeled
09:45 and evaluated at its 10:00 close.

Aggregation is causal by construction: a 15-minute bar is built only from the
1-minute bars whose timestamps fall inside its own [start, end) window.
"""

from __future__ import annotations

import pandas as pd

from orb import config
from orb.data import session

_OHLCV_AGG = {
    "Open": "first",
    "High": "max",
    "Low": "min",
    "Close": "last",
    "Volume": "sum",
}

_REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def resample_15m(df_1min: pd.DataFrame) -> pd.DataFrame:
    """Resample RTH 1-minute bars to 9:30-anchored 15-minute OHLCV bars.

    *df_1min* must have an ET-localized DatetimeIndex and columns
    Open/High/Low/Close/Volume. Returns a 15-minute frame with the same columns.
    """
    session.assert_clean_et_index(df_1min)
    missing = [c for c in _REQUIRED_COLUMNS if c not in df_1min.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    rth = session.to_rth(df_1min)
    if rth.empty:
        return rth.copy()

    rule = f"{config.DECISION_TF_MINUTES}min"
    out_frames = []
    # Resample per session so bins never span the overnight gap and origin is
    # the session's own midnight (09:30 lies on the 15-minute grid from midnight).
    for _, day in rth.groupby(session.session_date(rth.index)):
        agg = (
            day.resample(
                rule,
                label=config.BAR_LABEL.replace("interval_start", "left"),
                closed=config.BAR_INTERVAL_CLOSED,
                origin="start_day",
            )
            .agg(_OHLCV_AGG)
            .dropna(subset=["Open"])  # drop empty bins (e.g. data gaps)
        )
        out_frames.append(agg)

    result = pd.concat(out_frames).sort_index()
    return result
