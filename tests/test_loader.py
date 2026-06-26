"""Unit tests for the real-data normalization, validation, and coverage layer.

Synthetic frames only — the real network pull is in scripts/fetch_data.py and is
run separately. Focus: UTC->ET (incl. DST), RTH filter, OR/entry skip logic, the
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


def _full_morning(date, start="09:30", end="11:00"):
    """A session with a complete OR + entry window (09:30..11:00)."""
    idx = pd.date_range(f"{date} {start}", f"{date} {end}", freq="1min", tz=ET)
    return _et_bars(idx)


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


# --- strict validation: OR / entry completeness ---------------------------

def test_complete_session_is_usable():
    bars = _full_morning("2024-06-03")
    clean, skips, dates = loader.validate_sessions(bars, "SPY")
    assert skips == []
    assert len(dates) == 1
    assert not clean.empty


def test_missing_0930_bar_skips_or_window():
    bars = _full_morning("2024-06-03")
    bars = bars.drop(pd.Timestamp("2024-06-03 09:30", tz=ET))
    clean, skips, _ = loader.validate_sessions(bars, "SPY")
    assert clean.empty
    assert len(skips) == 1 and skips[0]["reason"] == "or_window_incomplete"


def test_or_window_gap_skips():
    bars = _full_morning("2024-06-03")
    bars = bars.drop(pd.Timestamp("2024-06-03 09:35", tz=ET))
    _, skips, _ = loader.validate_sessions(bars, "SPY")
    assert skips[0]["reason"] == "or_window_incomplete"
    assert "09:35" in skips[0]["detail"]


def test_entry_window_gap_skips():
    bars = _full_morning("2024-06-03")
    bars = bars.drop(pd.Timestamp("2024-06-03 10:30", tz=ET))
    _, skips, _ = loader.validate_sessions(bars, "SPY")
    assert skips[0]["reason"] == "entry_window_gap"
    assert "10:30" in skips[0]["detail"]


def test_or_takes_precedence_over_entry_in_reason():
    # both windows broken -> reported as OR incomplete (checked first)
    bars = _full_morning("2024-06-03")
    bars = bars.drop([pd.Timestamp("2024-06-03 09:31", tz=ET),
                      pd.Timestamp("2024-06-03 10:30", tz=ET)])
    _, skips, _ = loader.validate_sessions(bars, "SPY")
    assert skips[0]["reason"] == "or_window_incomplete"


def test_eleven_oclock_bar_not_required():
    # entry-window coverage is [09:45, 11:00) -> the 11:00 bar itself is optional
    idx = pd.date_range("2024-06-03 09:30", "2024-06-03 10:59", freq="1min", tz=ET)
    _, skips, _ = loader.validate_sessions(_et_bars(idx), "SPY")
    assert skips == []


# --- split-jump tripwire --------------------------------------------------

def test_split_jump_flagged():
    d1 = _full_morning("2024-06-03")                 # close 100
    d2 = _et_bars(pd.date_range("2024-06-04 09:30", "2024-06-04 11:00", freq="1min", tz=ET),
                  price=200.0)                        # open 200 -> +100% overnight
    clean = pd.concat([d1, d2]).sort_index()
    jumps = loader.detect_split_jumps(clean)
    assert len(jumps) == 1
    assert jumps[0]["overnight_return"] == pytest.approx(1.0)


def test_no_split_jump_when_normal():
    d1 = _full_morning("2024-06-03")
    d2 = _et_bars(pd.date_range("2024-06-04 09:30", "2024-06-04 11:00", freq="1min", tz=ET),
                  price=100.5)
    clean = pd.concat([d1, d2]).sort_index()
    assert loader.detect_split_jumps(clean) == []


# --- coverage report ------------------------------------------------------

def test_coverage_report_counts_and_per_year():
    usable_2023 = _full_morning("2023-06-01")
    skipped_2023 = _full_morning("2023-06-02").drop(pd.Timestamp("2023-06-02 10:30", tz=ET))
    usable_2024 = _full_morning("2024-06-03")
    frame = pd.concat([usable_2023, skipped_2023, usable_2024]).sort_index()

    clean, skips, dates = loader.validate_sessions(frame, "QQQ")
    cov = loader.build_coverage("QQQ", dates, skips, clean)

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
    idx = pd.date_range("2024-06-03 13:30", "2024-06-03 15:00", freq="1min", tz="UTC")  # 09:30-11:00 EDT
    raw = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0,
                        "close": 100.0, "volume": 1000.0}, index=idx)
    clean, skips, cov = loader.prepare_symbol(raw, "SPY")
    assert skips == []
    assert cov["usable_sessions"] == 1
    assert clean.index[0] == pd.Timestamp("2024-06-03 09:30", tz=ET)
