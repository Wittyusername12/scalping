"""Assembled ORB strategy for backtesting.py — executes on 1-minute bars using
precomputed 15-minute context, wiring in the pure decision functions.

This module does TWO things:

1. `build_execution_frame(...)` — the upstream alignment. It takes the 1-minute
   OHLCV frame, the precomputed 15-minute feature frame (indexed by 15-min START),
   the daily prior-day VIX, and produces the 1-minute execution frame the Strategy
   runs on, with each 15-min feature aligned so it becomes visible ONLY on the
   1-minute bar at or after the 15-min bar that produced it has fully CLOSED.
   This causal alignment is the single most important property here; it is proved
   by the no-lookahead suite.

2. `ORBStrategy` — a backtesting.py Strategy. In next() it reads the aligned
   context off self.data (which backtesting.py truncates to the current bar) and
   calls the existing pure functions (signals/sizing/fills) for every decision.
   It does NOT recompute any feature, buffer, filter, size, or fill price.

Position management and fills are EXPLICIT (per the spec/B2/I2): we do not use
backtesting.py bracket/managed orders. We track our own per-session state and
record each fill via fill_price; the library is used only to iterate bars and
truncate self.data (which is what enforces no lookahead at the engine level).
"""

from __future__ import annotations

from typing import NamedTuple, Optional

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy

from orb import config
from orb.strategy.fills import fill_price, stop_fill_reference
from orb.strategy.signals import evaluate_entry
from orb.strategy.sizing import position_size

# The 15-min feature columns aligned onto the 1-min frame.
FEATURE_COLUMNS = (
    "or_high", "or_low", "or_mid", "atr", "vwap", "vol_sma", "close15", "volume15",
)


class Action(NamedTuple):
    kind: str                  # "enter" | "stop" | "flatten" | "size_skip" | (none -> not recorded)
    price: Optional[float]
    shares: Optional[int]


class Trade(NamedTuple):
    entry_ts: pd.Timestamp
    entry_price: float
    shares: int
    exit_ts: pd.Timestamp
    exit_kind: str             # "stop" | "flatten"
    exit_price: float


# --------------------------------------------------------------------------
# Upstream alignment: 15-min features -> 1-min execution frame (CAUSAL)
# --------------------------------------------------------------------------

def build_execution_frame(
    min_bars: pd.DataFrame,
    features_15m: pd.DataFrame,
    vix_by_date: dict,
    leak_feature: Optional[str] = None,
) -> pd.DataFrame:
    """Align 15-min features onto the 1-min frame so each is visible only from
    the 1-min bar at/after its source 15-min bar's CLOSE.

    `features_15m` is indexed by the 15-min bar's START; its close is start+15min.
    For each 1-min bar we take the latest 15-min bar whose close-time <= the
    1-min timestamp (explicit backward as-of; exact match allowed). Bars before
    the first 15-min close get NaN.

    `leak_feature` (test-only): shift that aligned column one 1-min bar EARLY,
    injecting lookahead so the no-lookahead suite can prove it has teeth.
    """
    if not min_bars.index.is_monotonic_increasing:
        raise ValueError("min_bars index must be sorted ascending.")

    close_ns = (features_15m.index + pd.Timedelta(minutes=config.DECISION_TF_MINUTES)).asi8
    bar_ns = min_bars.index.asi8
    # rightmost 15-min bar with close-time <= each 1-min timestamp; -1 if none yet
    pos = np.searchsorted(close_ns, bar_ns, side="right") - 1
    valid = pos >= 0

    out = pd.DataFrame(index=min_bars.index)
    for col in ("Open", "High", "Low", "Close", "Volume"):
        out[col] = min_bars[col].astype(float).to_numpy()

    for col in FEATURE_COLUMNS:
        src = features_15m[col].to_numpy(dtype=float)
        aligned = np.full(len(min_bars), np.nan)
        aligned[valid] = src[pos[valid]]
        out[col] = aligned

    dates = min_bars.index.date
    out["prior_vix"] = np.array([vix_by_date.get(d, np.nan) for d in dates], dtype=float)

    times = min_bars.index.time
    first, last, exit_t = (
        config.ENTRY_WINDOW_FIRST_CLOSE,
        config.ENTRY_WINDOW_LAST_CLOSE,
        config.TIME_EXIT,
    )
    out["is_decision"] = np.array(
        [1.0 if (t.minute % config.DECISION_TF_MINUTES == 0 and t.second == 0
                 and first <= t <= last) else 0.0 for t in times],
        dtype=float,
    )
    out["is_flatten"] = np.array([1.0 if t == exit_t else 0.0 for t in times], dtype=float)

    if leak_feature is not None:
        out[leak_feature] = out[leak_feature].shift(-1)  # visible one 1-min bar early == lookahead

    return out


# --------------------------------------------------------------------------
# The Strategy
# --------------------------------------------------------------------------

class ORBStrategy(Strategy):
    """ORB execution on 1-min bars. Configure via the class attributes below
    (set by run_strategy); reads aligned context off self.data; decides via the
    pure functions."""

    skip_dates = frozenset()
    account_equity = config.ACCOUNT_EQUITY_GATE
    slippage_cents = config.SLIPPAGE_CENTS_PER_SHARE
    slippage_multiplier = 1.0

    def init(self):
        self._cur_date = None
        self._traded_today = False     # one qualifying breakout per day consumes the day
        self._in_pos = False
        self._shares = 0
        self._stop = float("nan")      # OR_low captured at entry
        self._entry_bar = -1           # position index of the entry bar (stop live from here)
        self._open = None              # (entry_ts, entry_price, shares) while in a position
        self.records = []              # [(ts, (Action, ...))] per processed bar -> replay
        self.round_trips = []          # [Trade] completed round-trips (Strategy.trades is reserved)

    # -- helpers ----------------------------------------------------------
    def _val(self, name):
        return float(getattr(self.data, name)[-1])

    def _flag(self, name):
        return getattr(self.data, name)[-1] > 0.5

    # -- per-bar logic ----------------------------------------------------
    def next(self):
        ts = self.data.index[-1]
        d = ts.date()
        i = len(self.data) - 1
        cents, mult = self.slippage_cents, self.slippage_multiplier

        # explicit per-session reset (date-keyed; no overnight leak)
        if d != self._cur_date:
            if self._in_pos:
                raise AssertionError(f"position leaked into new session {d}")
            self._cur_date = d
            self._traded_today = False

        actions = []
        or_low = self._val("or_low")
        low = self._val("Low")
        bar_open = self._val("Open")
        bar_close = self._val("Close")

        # ---- ENTRY: only on in-window 15-min decision bars, while flat ----
        if (not self._in_pos) and (not self._traded_today) and self._flag("is_decision"):
            inputs = [self._val(c) for c in FEATURE_COLUMNS] + [self._val("prior_vix")]
            if not any(np.isnan(x) for x in inputs):
                close15 = self._val("close15")
                decision = evaluate_entry(
                    close=close15,
                    or_high=self._val("or_high"),
                    or_low=or_low,
                    or_mid=self._val("or_mid"),
                    atr=self._val("atr"),
                    volume=self._val("volume15"),
                    volume_sma=self._val("vol_sma"),
                    vwap=self._val("vwap"),
                    prior_day_vix=self._val("prior_vix"),
                    session_date=d,
                    skip_dates=self.skip_dates,
                )
                if decision.enter:
                    self._traded_today = True  # first qualifying breakout consumes the day
                    fill = fill_price(close15, "buy", cents, mult)
                    shares = position_size(self.account_equity, fill, or_low)
                    if shares >= 1:
                        self._in_pos = True
                        self._shares = shares
                        self._stop = or_low
                        self._entry_bar = i
                        self._open = (ts, fill, shares)
                        actions.append(Action("enter", fill, shares))
                    else:
                        actions.append(Action("size_skip", fill, 0))

        # ---- STOP: live from the entry bar forward; gap-through fills worse --
        if self._in_pos and i >= self._entry_bar and low <= self._stop:
            ref = stop_fill_reference(self._stop, bar_open)
            px = fill_price(ref, "sell", cents, mult)
            actions.append(Action("stop", px, self._shares))
            self._close_position(ts, "stop", px)

        # ---- FLATTEN: 15:50; stop already had priority on a shared bar ------
        if self._in_pos and self._flag("is_flatten"):
            px = fill_price(bar_close, "sell", cents, mult)
            actions.append(Action("flatten", px, self._shares))
            self._close_position(ts, "flatten", px)

        self.records.append((ts, tuple(actions)))

    def _close_position(self, ts, kind, price):
        entry_ts, entry_price, shares = self._open
        self.round_trips.append(Trade(entry_ts, entry_price, shares, ts, kind, price))
        self._in_pos = False
        self._open = None


# --------------------------------------------------------------------------
# Runner (test helper; NOT the gate/runner)
# --------------------------------------------------------------------------

def run_strategy(
    exec_frame: pd.DataFrame,
    *,
    skip_dates=frozenset(),
    account_equity: float = config.ACCOUNT_EQUITY_GATE,
    slippage_cents: float = config.SLIPPAGE_CENTS_PER_SHARE,
    slippage_multiplier: float = 1.0,
) -> ORBStrategy:
    """Run ORBStrategy over an execution frame and return the strategy instance
    (with .records and .trades). Library cash is irrelevant (we manage fills)."""
    ORBStrategy.skip_dates = frozenset(skip_dates)
    ORBStrategy.account_equity = float(account_equity)
    ORBStrategy.slippage_cents = float(slippage_cents)
    ORBStrategy.slippage_multiplier = float(slippage_multiplier)
    bt = Backtest(exec_frame, ORBStrategy, cash=1_000_000_000.0, commission=0.0)
    stats = bt.run()
    return stats._strategy


def actions_by_ts(strat: ORBStrategy) -> dict:
    """Map each processed bar timestamp -> its tuple of Actions (for replay)."""
    return {ts: acts for ts, acts in strat.records}
