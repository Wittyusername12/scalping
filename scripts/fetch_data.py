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
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
YAHOO_VIX_URL = (
    "https://query1.finance.yahoo.com/v7/finance/download/%5EVIX"
    "?period1=1514764800&period2=9999999999&interval=1d&events=history"
)


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

    end = min(dt.datetime(year + 1, 1, 1), dt.datetime.now())
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=dt.datetime(year, 1, 1),
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
        if path.exists():
            frames.append(pd.read_parquet(path))
            print(f"  {symbol} {year}: cached")
            continue
        df = _fetch_year(client, symbol, year, Adjustment.SPLIT)
        df.to_parquet(path)
        frames.append(df)
        print(f"  {symbol} {year}: pulled {len(df)} bars")
    return pd.concat(frames).sort_index() if frames else pd.DataFrame()


def verify_no_split_distortion(client, symbol: str, this_year: int) -> None:
    """SPY/QQQ have no splits in this window, so split-adjusted == raw. Pull a
    sample year RAW and assert it equals the split-adjusted cache; any difference
    is a red flag, not normal."""
    from alpaca.data.enums import Adjustment

    raw_y = _fetch_year(client, symbol, this_year, Adjustment.RAW)
    split_y = pd.read_parquet(loader.raw_path(symbol, this_year))
    common = raw_y.index.intersection(split_y.index)
    if len(common) == 0:
        print(f"  {symbol}: no overlap to cross-check split-vs-raw")
        return
    diff = (raw_y.loc[common, "close"] - split_y.loc[common, "close"]).abs().max()
    flag = "" if diff < 1e-6 else "  !! RED FLAG: split-adjusted != raw"
    print(f"  {symbol}: split-vs-raw max close diff = {diff:.6g}{flag}")


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
    except Exception as exc:  # noqa: BLE001 — fall back to Yahoo
        print(f"  Cboe fetch failed ({exc}); trying Yahoo")
        raw = pd.read_csv(YAHOO_VIX_URL)
        out = pd.DataFrame({
            "date": pd.to_datetime(raw["Date"]).dt.date,
            "vix_close": raw["Close"].astype(float),
        })
        src = "Yahoo"
    out = out.dropna().sort_values("date")
    out = out[out["date"] >= dt.date(START_YEAR, 1, 1)]
    out.to_csv(loader.vix_csv_path(), index=False)
    print(f"  VIX: {len(out)} daily closes from {src} "
          f"({out['date'].iloc[0]} -> {out['date'].iloc[-1]})")
    # sanity: the prior-day join must work without same-day leakage
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
