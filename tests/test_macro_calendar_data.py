"""Verify the committed macro-event skip list (orb/external/data/macro_events.csv).

Locks in the compiled data so an accidental edit or a bad regeneration fails
loudly. Counts and known-date assertions mirror SOURCES.md.
"""

from __future__ import annotations

import datetime as dt

from orb import config
from orb.external import macro_calendar as mc


def _events():
    return mc.load_default_events()


def test_loads_and_event_types_valid():
    ev = _events()
    assert set(ev["event_type"].unique()) <= set(config.MACRO_SKIP_EVENT_TYPES)
    assert len(ev) == 284  # total rows (see SOURCES.md)


def test_counts_per_category():
    ev = _events()
    counts = ev["event_type"].value_counts().to_dict()
    assert counts["CPI"] == 107
    assert counts["NFP"] == 104
    assert counts["FOMC"] == 73


def test_counts_per_year():
    ev = _events()
    years = {d.year for d in ev["date"]}
    assert years == set(range(2018, 2027))
    by_year = {}
    for d, et in zip(ev["date"], ev["event_type"]):
        by_year.setdefault((d.year, et), 0)
        by_year[(d.year, et)] += 1
    # spot-check the years with non-standard counts (see SOURCES.md table)
    assert by_year[(2020, "FOMC")] == 11
    assert by_year[(2024, "NFP")] == 14
    assert by_year[(2025, "CPI")] == 11
    assert by_year[(2026, "FOMC")] == 4


def test_date_range_within_bounds():
    ev = _events()
    assert min(ev["date"]) >= dt.date(2018, 1, 1)
    assert max(ev["date"]) <= dt.date(2026, 6, 30)


def test_no_duplicate_rows():
    ev = _events()
    pairs = list(zip(ev["date"], ev["event_type"]))
    assert len(pairs) == len(set(pairs))


def test_known_included_fomc_dates_present():
    skip = mc.load_default_skip_dates()
    for d in [
        dt.date(2020, 3, 3),   # emergency cut
        dt.date(2020, 3, 15),  # emergency cut (Sunday)
        dt.date(2020, 3, 23),  # notation-vote Statement (QE)
        dt.date(2020, 8, 27),  # framework Statement
        dt.date(2025, 8, 22),  # framework Statement
        dt.date(2019, 10, 11), # unscheduled Statement
    ]:
        assert d in skip


def test_excluded_fomc_dates_absent():
    skip = mc.load_default_skip_dates()
    for d in [
        dt.date(2020, 3, 17),  # cancelled meeting
        dt.date(2020, 3, 18),  # cancelled meeting
        dt.date(2020, 3, 19),  # 'Press Release' only (swap lines)
        dt.date(2020, 3, 31),  # 'Press Release' only (FIMA repo)
    ]:
        assert d not in skip


def test_shutdown_delayed_dates_present():
    skip = mc.load_default_skip_dates()
    for d in [dt.date(2025, 10, 24), dt.date(2025, 12, 18),
              dt.date(2025, 11, 20), dt.date(2025, 12, 16)]:
        assert d in skip


def test_cpi_february_multidate_months():
    ev = _events()
    cpi_feb = sorted(d for d, et in zip(ev["date"], ev["event_type"])
                     if et == "CPI" and d.month == 2)
    # six years with TWO February CPI dates (seasonal revision + headline)
    by_year = {}
    for d in cpi_feb:
        by_year.setdefault(d.year, 0)
        by_year[d.year] += 1
    doubled = {y for y, n in by_year.items() if n == 2}
    assert doubled == {2019, 2020, 2021, 2022, 2023, 2024}


def test_skip_set_dedupes_collisions():
    ev = _events()
    skip = mc.load_default_skip_dates()
    # 3 dates are both CPI and FOMC -> 284 rows but 281 unique dates
    assert len(ev) == 284
    assert len(skip) == 281
