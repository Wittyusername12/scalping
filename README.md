# ORB v1 — backtesting.py harness

Implementation of the frozen opening-range-breakout strategy in
`ORB_v1_Phase0_Spec.md`, under the rules in `CLAUDE.md`. Plain Python:
`backtesting.py` + pandas/numpy only.

## Status — build step 1 of N

Per the agreed build order, the harness is being built **causality-first**: the
no-lookahead test suite must be green before any strategy logic or backtest is
written. A green causality suite is what earns the right to trust a backtest
number.

**This step delivers:**

- `orb/config.py` — every frozen strategy parameter and every resolved
  implementation convention, in one file. Nothing else hardcodes a parameter.
- Feature functions on the canonical 9:30-anchored 15-minute series:
  - `orb/features/indicators.py` — ATR(14) (Wilder/RMA, matches `ta.atr`),
    SMA(volume, 20) excluding the current bar.
  - `orb/features/vwap.py` — session VWAP (HLC3, reset 09:30, OR bars included).
- Data layer: `orb/data/session.py` (ET localization, session keying, RTH
  filter) and `orb/data/aggregate.py` (1-min → 9:30-anchored 15-min).
- External daily series: `orb/external/vix.py` (prior-trading-day VIX, no
  same-day leakage) and `orb/external/macro_calendar.py` (FOMC/CPI/NFP skip,
  ET-date matching).
- `tests/` — the causality / no-lookahead suite (with "teeth" tests proving the
  checks actually catch deliberate lookahead), plus a frozen-parameter tripwire.

**Not yet built (next steps):** the opening-range/filter/sizing/fill logic, the
`backtesting.py` Strategy, the runner/gate, and the validation suite (Monte
Carlo, Deflated Sharpe, ±20% nudge). Real data compilation is also deferred: the
authoritative FOMC/CPI/NFP date list (B11), real VIX history, and 1-minute bars
for 2018→present. The causality suite deliberately runs on committed synthetic
fixtures so it needs no network or real data.

## Run the tests

```bash
pip install -r requirements.txt   # for tests, pandas + numpy + pytest suffice
pytest
```

## Key resolved conventions

See the top of `orb/config.py` for the full list (each tagged with the decision
ID it resolves). The load-bearing ones: all bars localized to America/New_York
(DST-aware); bar timestamp = interval start, half-open `[start, end)`; ATR =
Wilder/RMA continuous; volume SMA = trailing 20 excluding the current bar; VWAP =
HLC3 reset at 09:30; prior-day VIX via strictly-backward as-of join; single
OOS cutoff at 2024-01-01; fixed (non-compounding) base equity.
