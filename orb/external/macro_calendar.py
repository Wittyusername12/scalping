"""Macro-event skip calendar for §4.5 (FOMC + CPI + NFP).

Decision (B11): the real FOMC/CPI/NFP date list for 2018-present is compiled from
authoritative published sources (Fed FOMC calendar; BLS release schedules), uses
the SCHEDULED dates the market knew in advance, includes FOMC emergency
announcements (e.g. March 2020), FOMC = announcement days only, is stored in a
version-controlled CSV, and flagged for spot-check. THAT COMPILATION IS A
SEPARATE DATA TASK and is intentionally not done here -- this module is the
loader + matching logic, and the no-lookahead test runs against a synthetic
fixture so it is deterministic and needs no network or real data.

No-lookahead property: the skip decision for a session depends ONLY on that
session's pre-known ET calendar date matched against the frozen list -- never on
any intraday outcome. Matching is on the ET session date, so a raw timestamp
near midnight UTC is attributed to the correct ET day.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd

from orb import config


def load_macro_events(path: str) -> pd.DataFrame:
    """Load the macro-event CSV into a frame with columns ['date','event_type'].

    Expected columns: date, event_type (one of FOMC/CPI/NFP), and optionally a
    source column for auditability. Dates are parsed to tz-naive calendar dates.
    """
    raw = pd.read_csv(path)
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["date"]).dt.date,
            "event_type": raw["event_type"].astype(str).str.upper().str.strip(),
        }
    )
    bad = set(df["event_type"]) - set(config.MACRO_SKIP_EVENT_TYPES)
    if bad:
        raise ValueError(
            f"Unexpected event types {sorted(bad)}; allowed: {config.MACRO_SKIP_EVENT_TYPES}"
        )
    return df


def skip_dates(events: pd.DataFrame) -> set[_dt.date]:
    """The set of ET calendar dates on which the whole session is skipped."""
    return set(events["date"].tolist())


def is_skip_session(et_date: _dt.date, skip_set: set[_dt.date]) -> bool:
    """True iff the given ET session date is a macro-skip day.

    The decision is a pure calendar lookup -- no intraday data is consulted, so
    it cannot leak future information.
    """
    return et_date in skip_set


def to_et_session_date(timestamp: pd.Timestamp) -> _dt.date:
    """The ET calendar date a (possibly UTC) timestamp belongs to.

    Ensures macro dates are matched against the ET session, not the UTC day.
    """
    ts = pd.Timestamp(timestamp)
    if ts.tz is None:
        raise ValueError("timestamp must be tz-aware to resolve its ET session date.")
    return ts.tz_convert(config.SESSION_TZ).date()
