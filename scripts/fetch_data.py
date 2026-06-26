"""Standalone local-run data acquisition for the ORB backtest.

RUN THIS ON A MACHINE WITH NETWORK ACCESS — the build environment's egress is
blocked for data.alpaca.markets / cdn.cboe.com. It pulls split-adjusted 1-minute
SPY & QQQ bars (2018-01-01 -> present) from Alpaca (SIP) and daily VIX history,
normalizes + strictly validates them via orb.data.loader (no logic re-derived
here), caches to parquet under the git-ignored data/ paths, and prints the
coverage report.

Setup:
    pip install alpaca-py pyarrow pandas
    export ALPACA_API_KEY=...        # never hardcode / print / commit (CLAUDE.md #3)
    export ALPACA_SECRET_KEY=...
    python scripts/fetch_data.py

Re-running reads the per-year raw cache instead of re-pulling. Keys are read from
the environment only and are never logged.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

import pandas as pd

from orb import config
from orb.data import loader
from orb.external import vix as vixmod

START_YEAR = 2018
# Keep one trading day before 2018-01-01 so the first session (2018-01-02) has a
# strictly-prior VIX close for the no-lookahead prior-day join.
VIX_LOWER_BOUND = dt.date(2017, 12, 29)
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
# Fallback: Stooq daily CSV (no auth). Columns: Date,Open,High,Low,Close,Volume.
# (Yahoo's v7 /finance/download CSV endpoint was decommissioned in 2023.)
STOOQ_VIX_URL = "https://stooq.com/q/d/l/?s=^vix&i=d"


# --------------------------------------------------------------------------
# Alpaca 1-minute bars
# --------------------------------------------------------------------------

def _alpaca_client():
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit("ERROR: set ALPACA_API_KEY and ALPACA_SECRET_KEY env vars.")
    from alpaca.data.historical import StockHistoricalDataClient
    return StockHistoricalDataClient(key, secret)  # keys never printed


def _fetch_year(client, symbol: str, year: int, adjustment) -> pd.DataFrame:
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed

    utc = dt.timezone.utc
    # tz-AWARE UTC bounds: a naive datetime.now() is LOCAL time that Alpaca reads
    # as UTC, which on an ET host would drop the most recent ~4-5h of bars.
    end = min(dt.datetime(year + 1, 1, 1, tzinfo=utc), dt.datetime.now(utc))
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=dt.datetime(year, 1, 1, tzinfo=utc),
        end=end,
        adjustment=adjustment,                 # Adjustment.SPLIT
        feed=DataFeed.SIP,                     # full history > 15 min old on free plans
    )
    # alpaca-py auto-paginates next_page_token internally.
    bars = client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return df
    if isinstance(df.index, pd.MultiIndex):    # (symbol, timestamp) -> timestamp
        df = df.xs(symbol, level="symbol")
    return df


def fetch_symbol(client, symbol: str, this_year: int) -> pd.DataFrame:
    """Per-year pulls with raw parquet caching for resume; returns combined raw."""
    from alpaca.data.enums import Adjustment

    loader.RAW_DIR.mkdir(parents=True, exist_ok=True)
    frames = []
    for year in range(START_YEAR, this_year + 1):
        path = loader.raw_path(symbol, year)
        # Never treat the in-progress current year as a complete cacheable
        # artifact -- always re-pull it so the dataset stays "... -> present".
        if path.exists() and year != this_year:
            frames.append(pd.read_parquet(path))
            print(f"  {symbol} {year}: cached")
            continue
        df = _fetch_year(client, symbol, year, Adjustment.SPLIT)
        if df.empty:
            sys.exit(f"ERROR: empty {symbol} {year} from Alpaca -- refusing to "
                     f"cache a zero-bar year (check keys / SIP entitlement / range).")
        df.to_parquet(path)
        frames.append(df)
        print(f"  {symbol} {year}: pulled {len(df)} bars")
    return pd.concat(frames).sort_index() if frames else pd.DataFrame()


def verify_no_split_distortion(client, symbol: str, this_year: int) -> None:
    """SPY/QQQ have no splits in this window, so split-adjusted == raw. Cross-check
    a COMPLETE prior year (this_year-1): pull it RAW and assert it matches the
    split-adjusted cache across all OHLC. Any difference is a red flag, not normal."""
    from alpaca.data.enums import Adjustment

    yr = this_year - 1
    split_path = loader.raw_path(symbol, yr)
    if not split_path.exists():
        print(f"  {symbol}: no cached {yr} to cross-check split-vs-raw")
        return
    raw_y = _fetch_year(client, symbol, yr, Adjustment.RAW)
    split_y = pd.read_parquet(split_path)
    common = raw_y.index.intersection(split_y.index)
    if len(common) == 0:
        print(f"  {symbol} {yr}: !! no overlap to cross-check split-vs-raw (investigate)")
        return
    cols = ["open", "high", "low", "close"]
    diff = (raw_y.loc[common, cols] - split_y.loc[common, cols]).abs().to_numpy().max()
    flag = "" if diff < 1e-6 else "  !! RED FLAG: split-adjusted != raw"
    print(f"  {symbol} {yr}: split-vs-raw max OHLC diff = {diff:.6g}{flag}")


# --------------------------------------------------------------------------
# VIX daily close
# --------------------------------------------------------------------------

def fetch_vix() -> pd.DataFrame:
    """Daily VIX close 2018->present from Cboe (Yahoo fallback); cache to CSV.

    Writes a clean two-column CSV (date,vix_close) consumed by external/vix.py.
    """
    loader.EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        raw = pd.read_csv(CBOE_VIX_URL)
        out = pd.DataFrame({
            "date": pd.to_datetime(raw["DATE"]).dt.date,
            "vix_close": raw["CLOSE"].astype(float),
        })
        src = "Cboe"
    except Exception as exc:  # noqa: BLE001 — fall back to Stooq
        print(f"  Cboe fetch failed ({exc}); trying Stooq")
        raw = pd.read_csv(STOOQ_VIX_URL)
        out = pd.DataFrame({
            "date": pd.to_datetime(raw["Date"]).dt.date,
            "vix_close": raw["Close"].astype(float),
        })
        src = "Stooq"
    out = out.dropna().sort_values("date")
    out = out[out["date"] >= VIX_LOWER_BOUND]   # keep one prior day before 2018-01-02
    if out.empty:
        raise SystemExit("VIX source returned no usable rows after the date filter "
                         "-- check the column mapping / source URL.")
    out.to_csv(loader.vix_csv_path(), index=False)
    print(f"  VIX: {len(out)} daily closes from {src} "
          f"({out['date'].iloc[0]} -> {out['date'].iloc[-1]})")
    # sanity: the prior-day join must load without error (no same-day leakage)
    vixmod.load_vix_csv(loader.vix_csv_path())
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    this_year = dt.datetime.now().year
    client = _alpaca_client()

    reports = []
    for symbol in config.BACKTEST_SYMBOLS:
        print(f"[{symbol}] fetching 1-min bars {START_YEAR}-01-01 -> present ...")
        raw = fetch_symbol(client, symbol, this_year)
        verify_no_split_distortion(client, symbol, this_year)
        clean, skips, cov = loader.prepare_symbol(raw, symbol, source_tz="UTC")
        loader.write_clean_cache(clean, symbol)
        reports.append(cov)

    print("\n[VIX] fetching daily close ...")
    fetch_vix()

    print("\n" + "=" * 60)
    print("DATA COVERAGE REPORT")
    print("=" * 60)
    for cov in reports:
        print(loader.format_coverage(cov))
        print()


if __name__ == "__main__":
    main()
