"""Real-data normalization, strict validation, and coverage reporting.

This module is PURE (no network, no SDK): it takes already-fetched raw bars (a
DataFrame) and turns them into a clean, ET-localized, RTH-only 1-minute frame,
refusing to let bad data become bad numbers. Network acquisition lives in the
standalone scripts (scripts/fetch_data.py) so it can run wherever egress is
allowed; this layer is unit-tested on synthetic frames.

Pipeline per symbol:
  raw bars --normalize_bars--> ET OHLCV  --to_rth--> RTH bars
           --validate_sessions--> (clean usable bars, skip log, coverage)

Validation (strict, per the spec's §6 data rules):
  - A session must have a 09:30 ET bar and a COMPLETE opening range (09:30-09:45).
  - The entry-window 1-min coverage (09:45-11:00) must have no gaps.
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


def validate_sessions(rth: pd.DataFrame, symbol: str):
    """Split RTH 1-min bars into usable vs skipped sessions.

    Returns (clean_bars, skips, session_dates):
      clean_bars    : RTH 1-min bars for usable sessions only (sorted)
      skips         : list of {symbol, date, reason, detail}
      session_dates : every ET session date seen (usable + skipped)
    A session is usable iff its OR window (09:30-09:45) is complete AND its
    entry-window 1-min coverage (09:45-11:00) has no gaps.
    """
    session.assert_clean_et_index(rth)
    keys = session.session_date(rth.index)

    usable_frames, skips, all_dates = [], [], []
    for d, g in rth.groupby(keys):
        all_dates.append(d)
        present = set(g.index.time)
        miss_or = sorted(OR_TIMES - present)
        miss_entry = sorted(ENTRY_TIMES - present)
        if miss_or:
            skips.append({"symbol": symbol, "date": d,
                          "reason": "or_window_incomplete",
                          "detail": f"missing {len(miss_or)}: {_fmt_missing(miss_or)}"})
        elif miss_entry:
            skips.append({"symbol": symbol, "date": d,
                          "reason": "entry_window_gap",
                          "detail": f"missing {len(miss_entry)}: {_fmt_missing(miss_entry)}"})
        else:
            usable_frames.append(g)

    clean = (pd.concat(usable_frames).sort_index()
             if usable_frames else rth.iloc[0:0].copy())
    return clean, skips, all_dates


def detect_split_jumps(clean: pd.DataFrame):
    """Flag overnight moves only a split could explain (defensive tripwire).

    Compares each usable session's first RTH open (09:30) to the prior usable
    session's last RTH close. Returns [{prev_date, next_date, overnight_return}]
    for |return| > SPLIT_JUMP_THRESHOLD. Should be empty for SPY/QQQ.
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
                              "overnight_return": ret})
    return jumps


# --- coverage report ------------------------------------------------------

def build_coverage(symbol: str, all_dates, skips, clean: pd.DataFrame) -> dict:
    """Assemble the coverage report for one symbol."""
    skip_dates = {s["date"] for s in skips}
    usable_dates = sorted(d for d in all_dates if d not in skip_dates)
    all_sorted = sorted(all_dates)

    reasons = {}
    for s in skips:
        reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1

    per_year = {}
    for d in all_sorted:
        y = d.year
        per_year.setdefault(y, {"usable": 0, "skipped": 0})
    for d in usable_dates:
        per_year[d.year]["usable"] += 1
    for s in skips:
        per_year[s["date"].year]["skipped"] += 1
    for y, c in per_year.items():
        tot = c["usable"] + c["skipped"]
        c["skip_rate"] = (c["skipped"] / tot) if tot else 0.0

    return {
        "symbol": symbol,
        "total_sessions": len(all_sorted),
        "usable_sessions": len(usable_dates),
        "skipped_sessions": len(skips),
        "skip_reasons": reasons,
        "per_year": per_year,
        "first_usable": usable_dates[0] if usable_dates else None,
        "last_usable": usable_dates[-1] if usable_dates else None,
        "split_jumps": detect_split_jumps(clean),
    }


def format_coverage(cov: dict) -> str:
    """Human-readable coverage report for one symbol."""
    lines = []
    lines.append(f"=== Coverage: {cov['symbol']} ===")
    lines.append(f"sessions: total={cov['total_sessions']} "
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
            lines.append(f"   {j['prev_date']} -> {j['next_date']}: "
                         f"{j['overnight_return']*100:+.1f}% overnight")
    else:
        lines.append("split-jump tripwire: clean (no flags)")
    return "\n".join(lines)


def prepare_symbol(raw: pd.DataFrame, symbol: str, source_tz: str = "UTC"):
    """Full pure pipeline for one symbol: normalize -> RTH -> validate -> coverage.

    Returns (clean_rth_bars, skips, coverage_dict). Does no I/O.
    """
    et = normalize_bars(raw, source_tz=source_tz)
    rth = to_rth(et)
    clean, skips, all_dates = validate_sessions(rth, symbol)
    cov = build_coverage(symbol, all_dates, skips, clean)
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
