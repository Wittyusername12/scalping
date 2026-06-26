"""Real-data normalization, strict validation, and coverage reporting.

This module is PURE (no network, no SDK): it takes already-fetched raw bars (a
DataFrame) and turns them into a clean, ET-localized, RTH-only 1-minute frame,
refusing to let bad data become bad numbers. Network acquisition lives in the
standalone scripts (scripts/fetch_data.py) so it can run wherever egress is
allowed; this layer is unit-tested on synthetic frames.

Pipeline per symbol:
  raw bars --normalize_bars--> ET OHLCV  --to_rth--> RTH bars
           --validate_sessions--> (clean usable bars, skip log, coverage)

Validation (strict). These are implementation choices that make the data safe
for the backtest -- not literal spec §6 text:
  - A session must have a 09:30 ET bar and a COMPLETE opening range (09:30-09:45).
  - The entry-window 1-min coverage (09:45-11:00) must have no gaps.
  - The 15:50 ET flatten bar must exist -- so a position can always be flattened
    and never leaks into the next session. Sessions without it (scheduled
    early-close half-days, or a 15:50 data gap) are SKIPPED; this is how the
    backtest's SKIP_HALF_DAYS intent is honored at the data layer, calendar-free.
  - Otherwise the whole session is SKIPPED and logged with the reason.
  - A defensive split-jump tripwire FLAGS (does not fix) any overnight move only
    a split could explain -- it must not fire for SPY/QQQ in this window.

Wires into orb.data.session (to_et, to_rth, session_date); does not reimplement.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from orb import config
from orb.data import session

OHLCV = ["Open", "High", "Low", "Close", "Volume"]

# --- data-QA constants (NOT strategy parameters) --------------------------
# Overnight |return| beyond this is treated as a split/adjustment artifact to
# FLAG (SPY/QQQ have no splits in 2018->present, so this should never fire).
SPLIT_JUMP_THRESHOLD = 0.20
_MAX_MISSING_LISTED = 8  # truncate the missing-minutes list in skip reasons

# --- cache locations (all git-ignored) ------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
EXTERNAL_DIR = DATA_DIR / "external"


def raw_path(symbol: str, year: int | None = None) -> Path:
    stem = f"{symbol}_1min_{year}" if year is not None else f"{symbol}_1min_raw"
    return RAW_DIR / f"{stem}.parquet"


def clean_cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol}_1min_rth.parquet"


def vix_csv_path() -> Path:
    return EXTERNAL_DIR / "vix.csv"


# --- expected within-session minute sets ----------------------------------

def _minute_times(start: dt.time, end: dt.time) -> set:
    """Set of datetime.time at 1-min steps over the half-open window [start, end)."""
    base = dt.datetime(2000, 1, 3)  # arbitrary weekday; date irrelevant
    cur = base.replace(hour=start.hour, minute=start.minute)
    stop = base.replace(hour=end.hour, minute=end.minute)
    out = set()
    while cur < stop:
        out.add(cur.time())
        cur += dt.timedelta(minutes=1)
    return out


# OR window 09:30-09:45 (15 bars) and entry-window 1-min coverage 09:45-11:00.
# The entry-window upper bound is exclusive: the last decision (11:00 close) is
# fed by the 15-min bar [10:45,11:00) = 1-min bars 10:45..10:59.
OR_TIMES = _minute_times(config.OR_START, config.OR_END)
ENTRY_TIMES = _minute_times(config.OR_END, config.ENTRY_WINDOW_LAST_CLOSE)

# Minutes the flatten leads the close (15:50 vs 16:00 => 10). On an early-close
# day the required flatten bar shifts by the same lead (13:00 close => 12:50).
_BASE = dt.date(2000, 1, 1)
FLATTEN_LEAD_MIN = int(
    (dt.datetime.combine(_BASE, config.RTH_END)
     - dt.datetime.combine(_BASE, config.TIME_EXIT)).total_seconds() // 60
)


def _required_exit_time(close_t: dt.time) -> dt.time:
    """The flatten bar a session must contain, given its scheduled close.

    Full day: 16:00 close -> 15:50 (= config.TIME_EXIT). Early close 13:00 -> 12:50.
    """
    base = dt.datetime.combine(_BASE, close_t)
    return (base - dt.timedelta(minutes=FLATTEN_LEAD_MIN)).time()


# --- NYSE (XNYS) calendar: the authoritative set of trading sessions ------

_NYSE = None


def _nyse_calendar():
    global _NYSE
    if _NYSE is None:
        import pandas_market_calendars as mcal  # heavy, lazy import
        _NYSE = mcal.get_calendar("XNYS")
    return _NYSE


def expected_sessions(start_date, end_date) -> dict:
    """XNYS trading sessions in [start_date, end_date] inclusive.

    Returns {date: {'close_time': ET time, 'early_close': bool}}. A date NOT in
    this dict was a market holiday (or weekend) and is correctly absent -- never
    a skip. A date IN this dict but missing from the data is a 'no_data' outage.
    """
    cal = _nyse_calendar()
    sched = cal.schedule(start_date=str(start_date), end_date=str(end_date))
    out = {}
    for ts, row in sched.iterrows():
        close_et = row["market_close"].tz_convert(config.SESSION_TZ).time()
        out[ts.date()] = {"close_time": close_et, "early_close": close_et != config.RTH_END}
    return out


# --- normalization --------------------------------------------------------

def normalize_bars(raw: pd.DataFrame, source_tz: str = "UTC") -> pd.DataFrame:
    """Raw vendor bars -> clean ET-localized OHLCV 1-min frame (all hours).

    Accepts a DatetimeIndex or a timestamp column; case-insensitive OHLCV column
    names (alpaca-py emits lowercase). Alpaca labels bars at the interval START
    and timestamps in UTC; we convert UTC->ET (DST-aware) via session.to_et.
    Duplicates dropped (keep first); sorted ascending.
    """
    df = raw.copy()

    if not isinstance(df.index, pd.DatetimeIndex):
        for cand in ("timestamp", "t", "time", "datetime", "Datetime", "Date"):
            if cand in df.columns:
                df = df.set_index(cand)
                break
        else:
            raise ValueError("raw bars need a DatetimeIndex or a timestamp column.")
    df.index = pd.DatetimeIndex(df.index)

    lower = {c.lower(): c for c in df.columns}
    rename = {lower[k]: want for k, want in
              (("open", "Open"), ("high", "High"), ("low", "Low"),
               ("close", "Close"), ("volume", "Volume")) if k in lower}
    df = df.rename(columns=rename)
    missing = [c for c in OHLCV if c not in df.columns]
    if missing:
        raise ValueError(f"raw bars missing columns: {missing}")
    df = df[OHLCV].astype(float)

    df = session.to_et(df, assume_tz=source_tz)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def to_rth(df_et: pd.DataFrame) -> pd.DataFrame:
    """Regular-hours filter (09:30-16:00 ET); thin wrapper over session.to_rth."""
    return session.to_rth(df_et)


# --- strict validation ----------------------------------------------------

def _fmt_missing(times) -> str:
    shown = ",".join(t.strftime("%H:%M") for t in times[:_MAX_MISSING_LISTED])
    if len(times) > _MAX_MISSING_LISTED:
        shown += f",...(+{len(times) - _MAX_MISSING_LISTED})"
    return shown


def validate_sessions(rth: pd.DataFrame, symbol: str, schedule: dict | None = None):
    """Split RTH 1-min bars into usable vs skipped sessions, against the XNYS calendar.

    Returns (clean_bars, skips, expected_dates, off_calendar):
      clean_bars    : RTH 1-min bars for usable sessions only (sorted)
      skips         : list of {symbol, date, reason, detail}
      expected_dates: XNYS trading days in the data's date range (the universe)
      off_calendar  : dates present in the data that XNYS says were NOT trading
                      days (data error; flagged, excluded from clean)

    The universe is the set of XNYS trading days between the first and last dates
    present in the data. For each:
      - absent in data        -> 'no_data' skip (a real outage, not a holiday)
      - early-close day        -> 'half_day' skip while SKIP_HALF_DAYS is True;
                                  otherwise validated against its actual close
      - OR window incomplete   -> 'or_window_incomplete'
      - entry-window gap        -> 'entry_window_gap'
      - missing flatten bar     -> 'exit_bar_missing' (flatten time tracks the
                                   scheduled close: 15:50 full day, 12:50 early)
      - else                    -> usable
    Holidays (not XNYS trading days) are simply not in the universe -> never skips.
    `schedule` may be injected (tests); otherwise it is the real XNYS calendar.
    """
    session.assert_clean_et_index(rth)
    if rth.empty:
        return rth.copy(), [], [], []

    groups = {d: g for d, g in rth.groupby(session.session_date(rth.index))}
    data_dates = sorted(groups)
    if schedule is None:
        schedule = expected_sessions(data_dates[0], data_dates[-1])
    expected = sorted(schedule)
    off_calendar = [d for d in data_dates if d not in schedule]

    usable_frames, skips = [], []
    for d in expected:
        g = groups.get(d)
        if g is None:
            skips.append({"symbol": symbol, "date": d, "reason": "no_data",
                          "detail": "expected XNYS trading day with no bars (outage)"})
            continue
        info = schedule[d]
        if info["early_close"] and config.SKIP_HALF_DAYS:
            skips.append({"symbol": symbol, "date": d, "reason": "half_day",
                          "detail": f"early close {info['close_time'].strftime('%H:%M')} ET "
                                    f"(SKIP_HALF_DAYS)"})
            continue
        present = set(g.index.time)
        miss_or = sorted(OR_TIMES - present)
        miss_entry = sorted(ENTRY_TIMES - present)
        req_exit = _required_exit_time(info["close_time"])
        if miss_or:
            skips.append({"symbol": symbol, "date": d, "reason": "or_window_incomplete",
                          "detail": f"missing {len(miss_or)}: {_fmt_missing(miss_or)}"})
        elif miss_entry:
            skips.append({"symbol": symbol, "date": d, "reason": "entry_window_gap",
                          "detail": f"missing {len(miss_entry)}: {_fmt_missing(miss_entry)}"})
        elif req_exit not in present:
            skips.append({"symbol": symbol, "date": d, "reason": "exit_bar_missing",
                          "detail": f"no {req_exit.strftime('%H:%M')} flatten bar "
                                    f"(close {info['close_time'].strftime('%H:%M')})"})
        else:
            usable_frames.append(g)

    clean = (pd.concat(usable_frames).sort_index()
             if usable_frames else rth.iloc[0:0].copy())
    return clean, skips, expected, off_calendar


def detect_split_jumps(clean: pd.DataFrame):
    """Flag overnight moves only a split could explain (defensive tripwire).

    Compares each usable session's first RTH open (09:30) to the prior usable
    session's last RTH close. Because skipped/holiday/weekend sessions collapse
    out of `clean`, a flagged pair may span more than one calendar day, so the
    field is `gap_return` (with `days_apart`), not strictly overnight. Returns
    pairs with |return| > SPLIT_JUMP_THRESHOLD. Should be empty for SPY/QQQ.
    """
    if clean.empty:
        return []
    keys = session.session_date(clean.index)
    first_open, last_close, dates = [], [], []
    for d, g in clean.groupby(keys):
        dates.append(d)
        first_open.append(float(g["Open"].iloc[0]))
        last_close.append(float(g["Close"].iloc[-1]))

    jumps = []
    for i in range(1, len(dates)):
        prev_c = last_close[i - 1]
        nxt_o = first_open[i]
        if prev_c > 0:
            ret = nxt_o / prev_c - 1.0
            if abs(ret) > SPLIT_JUMP_THRESHOLD:
                jumps.append({"prev_date": dates[i - 1], "next_date": dates[i],
                              "days_apart": (dates[i] - dates[i - 1]).days,
                              "gap_return": ret})
    return jumps


# --- coverage report ------------------------------------------------------

def build_coverage(symbol: str, expected_dates, skips, clean: pd.DataFrame,
                   off_calendar=()) -> dict:
    """Assemble the coverage report for one symbol.

    The universe is `expected_dates` (XNYS trading days in range), so
    usable + skipped == total_sessions and holidays never appear. `off_calendar`
    (bars on non-trading days) is reported separately as a data-integrity flag.
    """
    skip_dates = {s["date"] for s in skips}
    expected = sorted(expected_dates)
    usable_dates = sorted(d for d in expected if d not in skip_dates)

    reasons = {}
    for s in skips:
        reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1

    per_year = {}
    for d in expected:
        per_year.setdefault(d.year, {"usable": 0, "skipped": 0})
    for d in usable_dates:
        per_year[d.year]["usable"] += 1
    for s in skips:
        per_year[s["date"].year]["skipped"] += 1
    for c in per_year.values():
        tot = c["usable"] + c["skipped"]
        c["skip_rate"] = (c["skipped"] / tot) if tot else 0.0

    return {
        "symbol": symbol,
        "total_sessions": len(expected),
        "usable_sessions": len(usable_dates),
        "skipped_sessions": len(skips),
        "skip_reasons": reasons,
        "per_year": per_year,
        "first_usable": usable_dates[0] if usable_dates else None,
        "last_usable": usable_dates[-1] if usable_dates else None,
        "off_calendar": [str(d) for d in sorted(off_calendar)],
        "split_jumps": detect_split_jumps(clean),
    }


def format_coverage(cov: dict) -> str:
    """Human-readable coverage report for one symbol."""
    lines = []
    lines.append(f"=== Coverage: {cov['symbol']} ===")
    lines.append(f"sessions: total={cov['total_sessions']} (XNYS trading days) "
                 f"usable={cov['usable_sessions']} skipped={cov['skipped_sessions']}")
    lines.append(f"usable range: {cov['first_usable']} -> {cov['last_usable']}")
    if cov["skip_reasons"]:
        reasons = ", ".join(f"{k}={v}" for k, v in sorted(cov["skip_reasons"].items()))
        lines.append(f"skip reasons: {reasons}")
    else:
        lines.append("skip reasons: none")
    lines.append("per year   usable  skipped  skip_rate")
    for y in sorted(cov["per_year"]):
        c = cov["per_year"][y]
        lines.append(f"  {y}      {c['usable']:>6}  {c['skipped']:>7}  {c['skip_rate']*100:>7.2f}%")
    jumps = cov["split_jumps"]
    if jumps:
        lines.append(f"!! SPLIT-JUMP TRIPWIRE fired {len(jumps)}x (investigate; "
                     f"SPY/QQQ should have none):")
        for j in jumps[:10]:
            lines.append(f"   {j['prev_date']} -> {j['next_date']} ({j['days_apart']}d): "
                         f"{j['gap_return']*100:+.1f}%")
    else:
        lines.append("split-jump tripwire: clean (no flags)")
    if cov.get("off_calendar"):
        lines.append(f"!! OFF-CALENDAR: bars on {len(cov['off_calendar'])} non-XNYS-trading "
                     f"day(s) (data error): {cov['off_calendar'][:10]}")
    return "\n".join(lines)


def prepare_symbol(raw: pd.DataFrame, symbol: str, source_tz: str = "UTC"):
    """Full pure pipeline for one symbol: normalize -> RTH -> validate -> coverage.

    Returns (clean_rth_bars, skips, coverage_dict). Does no I/O.
    """
    et = normalize_bars(raw, source_tz=source_tz)
    rth = to_rth(et)
    clean, skips, expected, off_cal = validate_sessions(rth, symbol)
    cov = build_coverage(symbol, expected, skips, clean, off_cal)
    return clean, skips, cov


# --- parquet cache (filesystem only; needs pyarrow/fastparquet) -----------

def write_clean_cache(clean: pd.DataFrame, symbol: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = clean_cache_path(symbol)
    clean.to_parquet(path)
    return path


def read_clean_cache(symbol: str) -> pd.DataFrame:
    df = pd.read_parquet(clean_cache_path(symbol))
    session.assert_clean_et_index(df)
    return df
