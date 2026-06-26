"""No-lookahead tests for the two external daily series: VIX and macro calendar.

VIX (B10): a session must see the PRIOR trading session's close, never its own
same-day close. Macro (B11): the skip is a pre-known scheduled calendar matched
on the ET session date, independent of any intraday data.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from orb.external import macro_calendar, vix


# --------------------------------------------------------------------------
# VIX prior-day alignment
# --------------------------------------------------------------------------

def _vix_fixture() -> pd.DataFrame:
    # Five consecutive trading days with distinctive closes; a SENTINEL spike on
    # the day we will query, so any same-day leakage is unmistakable.
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26"]
            ),
            "vix_close": [15.0, 16.5, 99.0, 18.25, 19.0],  # 99.0 == sentinel
        }
    )


def test_vix_uses_prior_trading_day_not_same_day():
    vixdf = _vix_fixture()
    sessions = [dt.date(2026, 6, 24)]  # the sentinel day
    out = vix.attach_prior_day_vix(sessions, vixdf)

    # Must pick 06-23's 16.5, NEVER the same-day sentinel 99.0.
    assert out.loc[0, "prior_vix_close"] == 16.5
    assert out.loc[0, "prior_vix_close"] != 99.0


def test_vix_no_prior_history_yields_nan_not_same_day():
    vixdf = _vix_fixture()
    # Query the very first VIX date: there is no strictly-prior close.
    out = vix.attach_prior_day_vix([dt.date(2026, 6, 22)], vixdf)
    assert pd.isna(out.loc[0, "prior_vix_close"])  # never falls back to same-day


def test_vix_band_is_inclusive():
    assert vix.vix_ok(12.0) is True
    assert vix.vix_ok(28.0) is True
    assert vix.vix_ok(11.99) is False
    assert vix.vix_ok(28.01) is False
    assert vix.vix_ok(float("nan")) is False


def test_vix_prior_day_full_alignment():
    vixdf = _vix_fixture()
    sessions = [dt.date(2026, 6, 23), dt.date(2026, 6, 25), dt.date(2026, 6, 26)]
    out = vix.attach_prior_day_vix(sessions, vixdf).set_index("session_date")
    assert out.loc[dt.date(2026, 6, 23), "prior_vix_close"] == 15.0   # sees 06-22
    assert out.loc[dt.date(2026, 6, 25), "prior_vix_close"] == 99.0   # sees 06-24
    assert out.loc[dt.date(2026, 6, 26), "prior_vix_close"] == 18.25  # sees 06-25


# --------------------------------------------------------------------------
# Macro calendar matching
# --------------------------------------------------------------------------

def _macro_fixture(tmp_path) -> str:
    path = tmp_path / "macro_events.csv"
    pd.DataFrame(
        {
            "date": ["2026-06-23", "2026-06-25", "2026-06-26"],
            "event_type": ["CPI", "NFP", "FOMC"],
            "source": ["BLS", "BLS", "Fed"],
        }
    ).to_csv(path, index=False)
    return str(path)


def test_macro_skip_matches_et_session_date(tmp_path):
    events = macro_calendar.load_macro_events(_macro_fixture(tmp_path))
    skip = macro_calendar.skip_dates(events)

    assert macro_calendar.is_skip_session(dt.date(2026, 6, 23), skip) is True
    assert macro_calendar.is_skip_session(dt.date(2026, 6, 24), skip) is False
    assert macro_calendar.is_skip_session(dt.date(2026, 6, 26), skip) is True


def test_macro_date_resolved_in_et_not_utc():
    """A bar at 00:30 UTC belongs to the PREVIOUS ET calendar day."""
    ts_utc = pd.Timestamp("2026-06-26 00:30", tz="UTC")  # = 2026-06-25 20:30 ET
    assert macro_calendar.to_et_session_date(ts_utc) == dt.date(2026, 6, 25)


def test_macro_rejects_unknown_event_type(tmp_path):
    path = tmp_path / "bad.csv"
    pd.DataFrame({"date": ["2026-06-23"], "event_type": ["GDP"]}).to_csv(path, index=False)
    try:
        macro_calendar.load_macro_events(str(path))
        assert False, "expected ValueError for unknown event type"
    except ValueError:
        pass
