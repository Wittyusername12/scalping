"""Unit tests for the real-data normalization, validation, and coverage layer.

Synthetic frames only — the real network pull is in scripts/fetch_data.py and is
run separately. Focus: UTC->ET (incl. DST), RTH filter, OR/entry/exit skip logic,
the XNYS calendar (holiday vs no_data, calendar-driven early close), the
split-jump tripwire, and the coverage report.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from orb import config
from orb.data import loader

ET = config.SESSION_TZ


def _et_bars(index, price=100.0):
    return pd.DataFrame(
        {"Open": price, "High": price, "Low": price, "Close": price, "Volume": 1000.0},
        index=index,
    )


def _usable_day(date, price=100.0, start="09:30", end="15:59"):
    """A full RTH session: complete OR + entry windows AND the 15:50 flatten bar."""
    idx = pd.date_range(f"{date} {start}", f"{date} {end}", freq="1min", tz=ET)
    return _et_bars(idx, price)


def _full_schedule(dates):
    """Inject a synthetic XNYS schedule of normal (16:00) trading days."""
    return {pd.Timestamp(d).date(): {"close_time": dt.time(16, 0), "early_close": False}
            for d in dates}


# --- normalization: UTC -> ET (DST-aware) ---------------------------------

def test_normalize_utc_to_et_winter_est():
    # EST = UTC-5 ; 14:30 UTC -> 09:30 ET
    idx = pd.date_range("2024-01-03 14:30", periods=3, freq="1min", tz="UTC")
    raw = pd.DataFrame({"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}, index=idx)
    out = loader.normalize_bars(raw)
    assert str(out.index.tz) == ET
    assert out.index[0] == pd.Timestamp("2024-01-03 09:30", tz=ET)
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_normalize_utc_to_et_summer_edt():
    # EDT = UTC-4 ; 13:30 UTC -> 09:30 ET
    idx = pd.date_range("2024-07-01 13:30", periods=3, freq="1min", tz="UTC")
    raw = pd.DataFrame({"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}, index=idx)
    out = loader.normalize_bars(raw)
    assert out.index[0] == pd.Timestamp("2024-07-01 09:30", tz=ET)


def test_normalize_from_timestamp_column_and_dedup_sort():
    idx = pd.to_datetime(["2024-01-03 14:31", "2024-01-03 14:30", "2024-01-03 14:30"], utc=True)
    raw = pd.DataFrame({"timestamp": idx, "open": [2, 1, 9], "high": [2, 1, 9],
                        "low": [2, 1, 9], "close": [2, 1, 9], "volume": [1, 1, 1]})
    out = loader.normalize_bars(raw)
    assert out.index.is_monotonic_increasing
    assert not out.index.has_duplicates
    assert out["Open"].iloc[0] == 1.0  # first of the duplicate 14:30 kept


def test_to_rth_drops_pre_and_post_market():
    idx = pd.date_range("2024-01-03 08:00", "2024-01-03 17:00", freq="1min", tz=ET)
    rth = loader.to_rth(_et_bars(idx))
    assert rth.index[0].time() == dt.time(9, 30)
    assert rth.index[-1].time() == dt.time(15, 59)  # 16:00 exclusive


# --- strict validation: OR / entry / exit completeness (single trading day) -

def test_complete_session_is_usable():
    clean, skips, expected, off = loader.validate_sessions(_usable_day("2024-06-03"), "SPY")
    assert skips == [] and off == []
    assert expected == [dt.date(2024, 6, 3)]
    assert not clean.empty


def test_missing_0930_bar_skips_or_window():
    bars = _usable_day("2024-06-03").drop(pd.Timestamp("2024-06-03 09:30", tz=ET))
    clean, skips, _, _ = loader.validate_sessions(bars, "SPY")
    assert clean.empty
    assert len(skips) == 1 and skips[0]["reason"] == "or_window_incomplete"


def test_or_window_gap_skips():
    bars = _usable_day("2024-06-03").drop(pd.Timestamp("2024-06-03 09:35", tz=ET))
    _, skips, _, _ = loader.validate_sessions(bars, "SPY")
    assert skips[0]["reason"] == "or_window_incomplete" and "09:35" in skips[0]["detail"]


def test_entry_window_gap_skips():
    bars = _usable_day("2024-06-03").drop(pd.Timestamp("2024-06-03 10:30", tz=ET))
    _, skips, _, _ = loader.validate_sessions(bars, "SPY")
    assert skips[0]["reason"] == "entry_window_gap" and "10:30" in skips[0]["detail"]


def test_or_takes_precedence_over_entry_in_reason():
    bars = _usable_day("2024-06-03").drop([pd.Timestamp("2024-06-03 09:31", tz=ET),
                                           pd.Timestamp("2024-06-03 10:30", tz=ET)])
    _, skips, _, _ = loader.validate_sessions(bars, "SPY")
    assert skips[0]["reason"] == "or_window_incomplete"


def test_eleven_oclock_bar_not_required():
    bars = _usable_day("2024-06-03").drop(pd.Timestamp("2024-06-03 11:00", tz=ET))
    _, skips, _, _ = loader.validate_sessions(bars, "SPY")
    assert skips == []


def test_exit_bar_missing_on_full_day_skips():
    # a 15:50 gap on a normal trading day -> can't flatten -> skip
    bars = _usable_day("2024-06-03").drop(pd.Timestamp("2024-06-03 15:50", tz=ET))
    _, skips, _, _ = loader.validate_sessions(bars, "SPY")
    assert skips[0]["reason"] == "exit_bar_missing" and "15:50" in skips[0]["detail"]


# --- XNYS calendar: holiday vs no_data, early-close handling --------------

def test_holiday_absent_and_outage_is_no_data():
    # Real July 2024: 07-04 is a holiday (correctly absent), 07-03 an early close,
    # 07-05 a normal trading day. We provide 07-02, 07-03, 07-08 and OMIT 07-05
    # (a trading day -> outage) and 07-04 (a holiday -> not a skip).
    frame = pd.concat([
        _usable_day("2024-07-02"),
        _et_bars(pd.date_range("2024-07-03 09:30", "2024-07-03 13:00", freq="1min", tz=ET)),
        _usable_day("2024-07-08"),
    ]).sort_index()
    clean, skips, expected, off = loader.validate_sessions(frame, "SPY")

    assert dt.date(2024, 7, 4) not in expected          # holiday: not in the universe
    assert off == []                                     # no off-calendar bars
    reasons = {s["date"]: s["reason"] for s in skips}
    assert reasons[dt.date(2024, 7, 5)] == "no_data"     # trading day, absent -> outage
    assert reasons[dt.date(2024, 7, 3)] == "half_day"    # early close, SKIP_HALF_DAYS

    cov = loader.build_coverage("SPY", expected, skips, clean, off)
    assert cov["total_sessions"] == 4                    # 07-02,03,05,08 (not 07-04)
    assert cov["usable_sessions"] == 2                   # 07-02, 07-08
    assert cov["skip_reasons"] == {"half_day": 1, "no_data": 1}


def test_early_close_half_day_skipped_by_default():
    # 2024-07-03 closes 13:00; SKIP_HALF_DAYS is True -> skipped as half_day
    bars = _et_bars(pd.date_range("2024-07-03 09:30", "2024-07-03 13:00", freq="1min", tz=ET))
    clean, skips, _, _ = loader.validate_sessions(bars, "SPY")
    assert clean.empty
    assert skips[0]["reason"] == "half_day" and "13:00" in skips[0]["detail"]


def test_early_close_validated_against_actual_close_when_traded(monkeypatch):
    # With SKIP_HALF_DAYS off, an early-close day is validated against its real
    # 13:00 close -> the required flatten bar is 12:50, not 15:50.
    monkeypatch.setattr(config, "SKIP_HALF_DAYS", False)
    bars = _et_bars(pd.date_range("2024-07-03 09:30", "2024-07-03 13:00", freq="1min", tz=ET))
    _, skips, _, _ = loader.validate_sessions(bars, "SPY")
    assert skips == []  # 12:50 flatten bar present -> usable

    bars_gap = bars.drop(pd.Timestamp("2024-07-03 12:50", tz=ET))
    _, skips2, _, _ = loader.validate_sessions(bars_gap, "SPY")
    assert skips2[0]["reason"] == "exit_bar_missing" and "12:50" in skips2[0]["detail"]


def test_off_calendar_bars_flagged():
    # bars on 2024-07-04 (a holiday) -> off-calendar, excluded from clean
    frame = pd.concat([
        _usable_day("2024-07-03", start="09:30", end="15:59"),  # provide a normal-shaped day...
        _usable_day("2024-07-05"),
    ]).sort_index()
    # inject a schedule that says 07-03 was NOT a trading day, 07-05 was
    sched = _full_schedule(["2024-07-05"])
    clean, skips, expected, off = loader.validate_sessions(frame, "SPY", schedule=sched)
    assert off == [dt.date(2024, 7, 3)]
    assert expected == [dt.date(2024, 7, 5)]
    cov = loader.build_coverage("SPY", expected, skips, clean, off)
    assert cov["off_calendar"] == ["2024-07-03"]


# --- split-jump tripwire --------------------------------------------------

def test_split_jump_flagged():
    clean = pd.concat([_usable_day("2024-06-03", price=100.0),
                       _usable_day("2024-06-04", price=200.0)]).sort_index()
    jumps = loader.detect_split_jumps(clean)
    assert len(jumps) == 1
    assert jumps[0]["gap_return"] == pytest.approx(1.0)
    assert jumps[0]["days_apart"] == 1


def test_no_split_jump_when_normal():
    clean = pd.concat([_usable_day("2024-06-03", price=100.0),
                       _usable_day("2024-06-04", price=100.5)]).sort_index()
    assert loader.detect_split_jumps(clean) == []


# --- coverage report (injected schedule to avoid full-range flooding) -------

def test_coverage_report_counts_and_per_year():
    frame = pd.concat([
        _usable_day("2023-06-01"),
        _usable_day("2023-06-02").drop(pd.Timestamp("2023-06-02 10:30", tz=ET)),  # entry gap
        _usable_day("2024-06-03"),
    ]).sort_index()
    sched = _full_schedule(["2023-06-01", "2023-06-02", "2024-06-03"])

    clean, skips, expected, off = loader.validate_sessions(frame, "QQQ", schedule=sched)
    cov = loader.build_coverage("QQQ", expected, skips, clean, off)

    assert cov["total_sessions"] == 3
    assert cov["usable_sessions"] == 2
    assert cov["skipped_sessions"] == 1
    assert cov["skip_reasons"] == {"entry_window_gap": 1}
    assert cov["per_year"][2023] == {"usable": 1, "skipped": 1, "skip_rate": 0.5}
    assert cov["per_year"][2024]["skip_rate"] == 0.0
    assert cov["first_usable"] == dt.date(2023, 6, 1)
    assert cov["last_usable"] == dt.date(2024, 6, 3)
    text = loader.format_coverage(cov)
    assert "Coverage: QQQ" in text and "entry_window_gap=1" in text


def test_prepare_symbol_end_to_end_on_synthetic():
    # raw UTC bars -> normalize -> RTH -> validate -> coverage, all in one
    idx = pd.date_range("2024-06-03 13:30", "2024-06-03 19:59", freq="1min", tz="UTC")  # 09:30-15:59 EDT
    raw = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0,
                        "close": 100.0, "volume": 1000.0}, index=idx)
    clean, skips, cov = loader.prepare_symbol(raw, "SPY")
    assert skips == []
    assert cov["usable_sessions"] == 1
    assert clean.index[0] == pd.Timestamp("2024-06-03 09:30", tz=ET)
