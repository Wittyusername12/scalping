"""Prior-day VIX close for the §4.4 volatility-regime filter.

Decision (B10): source = Cboe official daily VIX close (Yahoo ^VIX an acceptable
fallback). "Prior-day" = the prior TRADING session's close, shifted one trading
day and as-of joined onto each equity session, so a session NEVER sees its own
same-day VIX (that would be a forbidden lookahead, CLAUDE.md rule 5). Band 12-28
inclusive at 2 decimals.

The join is implemented with merge_asof(direction='backward',
allow_exact_matches=False): for session date d it picks the VIX close on the
latest VIX date strictly less than d. If no prior VIX exists, the result is NaN
(we never fall back to same-day).
"""

from __future__ import annotations

import pandas as pd

from orb import config


def vix_ok(value: float) -> bool:
    """True iff prior-day VIX is within the frozen [12, 28] band (inclusive)."""
    if value is None or pd.isna(value):
        return False
    v = round(float(value), config.VIX_DECIMALS)
    return config.VIX_MIN <= v <= config.VIX_MAX


def load_vix_csv(path: str, date_col: str = "date", close_col: str = "vix_close") -> pd.DataFrame:
    """Load a daily VIX CSV into a sorted frame with columns ['date','vix_close'].

    `date` is parsed to tz-naive calendar dates (the trading day), `vix_close`
    to float. Duplicates and unsorted rows are not tolerated silently.
    """
    raw = pd.read_csv(path)
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(raw[date_col]).dt.normalize(),
            "vix_close": raw[close_col].astype(float),
        }
    ).sort_values("date").reset_index(drop=True)
    if df["date"].duplicated().any():
        raise ValueError("Duplicate dates in VIX series.")
    return df


def attach_prior_day_vix(session_dates, vix: pd.DataFrame) -> pd.DataFrame:
    """Map each equity session date to the prior trading session's VIX close.

    Returns a frame indexed like *session_dates* with columns:
      - session_date     : the equity session date (tz-naive, normalized)
      - prior_vix_close  : VIX close of the latest VIX date strictly before it
      - vix_ok           : whether that prior close is inside the [12,28] band

    Guarantees no same-day leakage via allow_exact_matches=False.
    """
    left = pd.DataFrame(
        {
            "session_date": pd.to_datetime(pd.Index(session_dates))
            .normalize()
            .astype("datetime64[ns]")
        }
    ).sort_values("session_date").reset_index(drop=True)

    right = vix.rename(columns={"date": "vix_date"}).copy()
    right["vix_date"] = pd.to_datetime(right["vix_date"]).astype("datetime64[ns]")
    right = right.sort_values("vix_date")

    merged = pd.merge_asof(
        left,
        right,
        left_on="session_date",
        right_on="vix_date",
        direction="backward",
        allow_exact_matches=False,  # strictly prior trading day -- no same-day VIX
    )
    merged = merged.rename(columns={"vix_close": "prior_vix_close"})
    merged["session_date"] = merged["session_date"].dt.date  # python dates (matches macro contract)
    merged["vix_ok"] = merged["prior_vix_close"].apply(vix_ok)
    return merged[["session_date", "prior_vix_close", "vix_ok"]]
