"""Single home for every frozen strategy parameter and every resolved
implementation convention for the ORB v1 backtest.

There are three kinds of values in this file, kept visually separate:

  1. FROZEN STRATEGY PARAMETERS  -- from ORB_v1_Phase0_Spec.md §3-§5, §7.
     Editing any of these CHANGES THE STRATEGY and violates CLAUDE.md rule 1.
     If you think one is wrong, say so and stop -- do not edit it here.

  2. RESOLVED CONVENTIONS         -- implementation decisions that the spec left
     open and the human resolved by ID (B1-B15, I1-I15, G/M). These are *how* we
     implement the frozen rules, not the rules themselves. They are pinned here
     so the whole harness reads them from one place.

  3. MODELING / DATA SETTINGS     -- cost assumptions, account sizes, instrument
     price levels, RNG seed, etc. Not strategy parameters; documented as such.
     A few are marked VERIFY: confirm before the gate run.

No magic numbers anywhere else in the codebase -- import from here.
"""

from __future__ import annotations

import datetime as _dt
import os as _os

_PKG_DIR = _os.path.dirname(_os.path.abspath(__file__))


# =============================================================================
# 1. FROZEN STRATEGY PARAMETERS  (spec §3-§5, §7)  -- DO NOT TUNE (CLAUDE.md #1)
# =============================================================================

# --- Session (§3) ---------------------------------------------------------
# US regular hours only, 9:30-16:00 ET. No pre/post-market.
RTH_START = _dt.time(9, 30)        # inclusive
RTH_END = _dt.time(16, 0)          # exclusive (last RTH minute bar is 15:59)

# --- Opening range (§3) ---------------------------------------------------
# First 15 minutes, 9:30:00-9:45:00 ET. OR_high = highest high, OR_low = lowest
# low of that window. The OR is exactly the first 15-minute decision bar.
OR_START = _dt.time(9, 30)
OR_END = _dt.time(9, 45)

# --- Decision timeframe (§3) ----------------------------------------------
DECISION_TF_MINUTES = 15

# --- Entry signal & buffer (§3) -------------------------------------------
# Entry on the close of a 15-min bar after 9:45 that closes strictly above
# OR_high + buffer. First qualifying breakout of the day only.
ATR_PERIOD = 14                    # ATR(14) on the 15-min series
BUFFER_ATR_MULT = 0.10            # buffer = 0.10 * ATR(14)...
BUFFER_FLOOR_DOLLARS = 0.05       # ...with a MINIMUM of $0.05 (a floor, i.e. max(0.10*ATR, 0.05))

# --- Entry window (§3) ----------------------------------------------------
# New entries only for bars closing after 9:45 and no later than 11:00 ET.
# Resolved (B1): eligible decision bars are those closing in (09:45, 11:00];
# first decision bar closes at 10:00, last eligible closes at 11:00.
ENTRY_WINDOW_FIRST_CLOSE = _dt.time(10, 0)   # 09:45-10:00 bar, evaluated at 10:00 close
ENTRY_WINDOW_LAST_CLOSE = _dt.time(11, 0)    # 10:45-11:00 bar, last eligible

# --- Exits (§3) -----------------------------------------------------------
PROFIT_TARGET = None              # none in v1; winners run to the time exit
TIME_EXIT = _dt.time(15, 50)      # hard flatten 15:50 ET, regardless of broker leg
# Stop is structural OR_low (no numeric parameter): per_share_risk = entry - OR_low.

# --- Frequency / concurrency (§3) -----------------------------------------
MAX_TRADES_PER_DAY_PER_SYMBOL = 1
ALLOW_REENTRY_AFTER_STOP = False
# "1 concurrent position across the whole system" is enforced by the live bridge
# in Phase 2, NOT in the backtest: per B6 the backtest runs each symbol standalone.
SYSTEM_MAX_CONCURRENT_POSITIONS = 1

# --- Filter 1: volume confirmation (§4.1) ---------------------------------
VOLUME_SMA_WINDOW = 20            # SMA(volume, 20) on the 15-min series
VOLUME_MULTIPLE = 1.3             # breakout-bar volume >= 1.3 * SMA(volume, 20)

# --- Filter 2: OR-width (§4.2) --------------------------------------------
# Skip the whole day if OR width is < 0.15% or > 1.0% of the OR midpoint price.
# Resolved (M2): midpoint = (OR_high + OR_low) / 2; band inclusive [0.15%, 1.0%].
OR_WIDTH_MIN_PCT = 0.15           # percent of OR midpoint
OR_WIDTH_MAX_PCT = 1.0            # percent of OR midpoint

# --- Filter 3: VWAP trend gate (§4.3) -------------------------------------
# Breakout bar must close ABOVE session VWAP. (No numeric parameter.)

# --- Filter 4: volatility regime / VIX (§4.4) -----------------------------
# Prior-day VIX close between 12 and 28. Resolved (B10): inclusive band.
VIX_MIN = 12.0
VIX_MAX = 28.0

# --- Filter 5: macro-event skip (§4.5) ------------------------------------
# Skip the entire session on scheduled FOMC + CPI + NFP days.
MACRO_SKIP_EVENT_TYPES = ("FOMC", "CPI", "NFP")

# --- Position sizing (§5) -------------------------------------------------
RISK_PER_TRADE = 0.005            # 0.5% of account_equity risked per trade
NOTIONAL_CAP_PCT = 0.30           # 30% max position notional
# shares = min(floor(risk$/per_share_risk), floor(0.30*equity/entry)); skip if < 1.

# --- Anti-overfitting (§7) ------------------------------------------------
# The values above are FROZEN BEFORE BACKTESTING and are never optimized.


# =============================================================================
# 2. RESOLVED CONVENTIONS  (human decisions by ID -- implementation, not strategy)
# =============================================================================

# --- Time / bars (B1) -----------------------------------------------------
SESSION_TZ = "America/New_York"   # DST-aware; never a fixed UTC offset
BAR_LABEL = "interval_start"      # a bar's timestamp marks its START
BAR_INTERVAL_CLOSED = "left"      # half-open [start, end)

# --- Execution timeframe (B2) ---------------------------------------------
# Signals/indicators computed on the 9:30-anchored 15-min series; execution
# (stop monitoring, 15:50 flatten) on 1-minute bars.
EXECUTION_TF_MINUTES = 1
# Fallback if free 1-min for 2018-present is unavailable: 15-min with the stop
# approximated by the bar low and EOD exit at the 15:45 close (documented
# conservative approximation). The harness prefers 1-min.
EXECUTION_FALLBACK_15M = False

# --- Indicator semantics --------------------------------------------------
ATR_METHOD = "wilder"             # B7: Wilder/RMA (matches TradingView ta.atr)
ATR_CONTINUOUS = True             # B7: continuous 15-min series; not reset per session
VOLUME_SMA_EXCLUDES_CURRENT_BAR = True   # B8: trailing 20 bars STRICTLY prior
VOLUME_SMA_CONTINUOUS = True      # B8: continuous across sessions
VWAP_PRICE_BASIS = "hlc3"         # B9: (High+Low+Close)/3 (matches ta.vwap)
VWAP_RESETS_AT = OR_START         # B9: reset 09:30 ET each session
VWAP_INCLUDES_OR_BARS = True      # B9: accumulate from the 9:30 OR bar inclusive

# --- Breakout comparison (M1) ---------------------------------------------
BREAKOUT_STRICTLY_ABOVE = True    # close > OR_high + buffer (strict)
ATR_AT_BREAKOUT_BAR = True        # I1: buffer uses ATR as of the breakout bar

# --- Filter evaluation (I4) -----------------------------------------------
# Reading A: entry = first bar in the entry window that breaks out AND passes
# all five filters; an earlier breakout that fails a filter does NOT end the day.
FIRST_QUALIFYING_BREAKOUT_READING = "A"

# --- VIX alignment (B10) --------------------------------------------------
VIX_USE_PRIOR_TRADING_DAY = True  # as-of join, strictly prior session; same-day forbidden
VIX_DECIMALS = 2

# --- Sizing mechanics (B4, B5, I3, M4) ------------------------------------
EQUITY_MODE = "fixed"             # B4: fixed base equity, no compounding
ACCOUNT_EQUITY_GATE = 5_000.0     # B4: base equity for the gate
ACCOUNT_EQUITY_SIDECAR = 3_000.0  # B4: robustness sidecar (report trade count + PF)
ENTRY_PRICE_IS_SLIPPED_FILL = True  # I3: per_share_risk uses the slipped fill price
ASSERT_PER_SHARE_RISK_POSITIVE = True  # M4: assert entry > OR_low; no new min-stop param
SIZE_COMPUTED_IN_OUR_CODE = True  # B5: pass an explicit integer size to backtesting.py
SKIP_IF_SHARES_BELOW_ONE = True   # §5 / B5
FAIL_ON_SILENT_ORDER_CANCEL = True  # B5: hard assertion if the library drops an order

# --- Out-of-sample reservation (B13, I8) ----------------------------------
OOS_START = _dt.date(2024, 1, 1)  # single hard cutoff: IS < 2024-01-01, OOS >= 2024-01-01
ALLOW_PRE_OOS_AS_WARMUP = True    # I8: pre-2024 bars/VIX loadable as lookback only
WALKFORWARD_FITS_ANYTHING = False # B13: rolling windows are report-only, no fitting
GATE_DECIDED_ON = "oos_2024_present"

# --- Half-days (I6) -------------------------------------------------------
SKIP_HALF_DAYS = True             # skip scheduled early-close (1:00pm) sessions


# =============================================================================
# 3. MODELING / DATA SETTINGS  (not strategy parameters)
# =============================================================================

# --- Macro-event skip calendar (§4.5, B11) --------------------------------
# Committed, version-controlled FOMC + CPI + NFP date list (2018-present).
# Compiled from authoritative sources; see orb/external/data/SOURCES.md.
MACRO_EVENTS_PATH = _os.path.join(_PKG_DIR, "external", "data", "macro_events.csv")

# --- Symbols (§2) ---------------------------------------------------------
BACKTEST_SYMBOLS = ("SPY", "QQQ")  # gate must pass on both (standalone, B6)

# --- Price adjustment (I7) ------------------------------------------------
PRICE_ADJUSTMENT = "split_only"   # no dividend back-adjustment; same vendor for SPY & QQQ

# --- Fill model (§6, B3) --------------------------------------------------
# Realistic vs pessimistic = two separate full runs, identical except this flag.
TRADE_ON_CLOSE_REALISTIC = True   # fill at signal-bar close (+ slippage)
TRADE_ON_CLOSE_PESSIMISTIC = False  # fill at next-bar open (+ slippage)
COMMISSION = 0.0                  # §5 / G5: explicitly zero
# Slippage is an ABSOLUTE cents/share price offset applied on EVERY fill, both
# legs (B3) -- never the library's relative spread. The base is a modeled cost
# assumption (NOT a frozen strategy parameter); the multiplier is the real stress.
SLIPPAGE_CENTS_PER_SHARE = 2.0    # one-way base in CENTS/share (fill_price divides by 100); upper end for these ETFs / SPYM
SLIPPAGE_STRESS_MULTIPLIERS = (1, 2, 3)  # 1x / 2x / 3x  ->  2.0 / 4.0 / 6.0 cents/share
GATE_SLIPPAGE_MULTIPLIER = 2      # §8: expectancy must be positive after 2x slippage

# --- Live instrument price levels for the gate (I11) ----------------------
# Backtest on SPY/QQQ history but scale to the live instrument's price level so
# whole-share rounding, the 30% cap, and skip-if-<1 are realistic for what we
# trade. VERIFY current prices before the gate run (§2 says verify).
LIVE_PRICE_LEVEL = {
    "SPY": 78.0,    # SPYM ~ $78  (VERIFY)
    "QQQ": 195.0,   # QQQM ~ $190s (VERIFY current)
}
REPORT_PERCENT_R_DIAGNOSTIC = True  # I11: also report pure %/R expectancy

# --- Monte Carlo (§6, §8, I12, G3) ---------------------------------------
MC_PATHS = 10_000                 # 10,000 reshuffles
MC_METHOD = "bootstrap_with_replacement"   # I12
MC_RESAMPLE_UNIT = "per_trade_dollar_pnl"  # I12: per-trade $ P&L
MC_COMPOUNDING = "additive_fixed_base"     # I12: additive on the fixed base
MC_MAX_DRAWDOWN_GATE_PCT = 15.0   # §8: 95th-pct max drawdown < 15%
MC_DRAWDOWN_CURVE = "closed_trade_equity"  # G3: gate measured on closed-trade equity
MC_RUIN_PEAK_TO_TROUGH_PCT = 15.0 # I12: "ruin" = breaching -15% peak-to-trough
RNG_SEED = 20260626               # I13: fixed seed; recorded in the report

# --- Gate thresholds (§8) -------------------------------------------------
GATE_MIN_PROFIT_FACTOR = 1.10
GATE_MIN_OOS_TRADES = 100
PF_EXPECTANCY_BASIS = "dollars_realized_after_slippage"  # B14
AFTER_2X_SLIPPAGE_IS_FULL_RERUN = True  # B14

# --- Deflated Sharpe (§6, §8, B15) ----------------------------------------
DSR_SHARPE_INPUT = "annualized_oos"   # B15: deflate the annualized OOS Sharpe
DSR_N_FROM_RESEARCH_LOG = True        # B15: N = count of distinct configs from the log
DSR_NUDGES_COUNT_AS_TRIALS = True     # B15: each +/-20% nudge counts as a trial
DSR_PASS_FLOOR = 0.95                 # B15: DSR >= 0.95 == "meaningfully > 0"

# --- +/-20% filter-nudge robustness (§8 criterion 7, I10) -----------------
# One-shot, report-only; the main run NEVER changes a parameter. Applies only to
# the five §4 filter thresholds, nudged one at a time. "Flip" = OOS expectancy
# changes sign OR profit factor crosses below 1.0.
NUDGE_FACTORS = (0.8, 1.2)
NUDGE_APPLIES_TO_FILTERS_ONLY = True
NUDGE_FLIP_DEFINITION = "expectancy_sign_change_or_pf_below_1"

# --- Reproducibility / research log (§7, I13) -----------------------------
RESEARCH_LOG_PATH = "research_log.jsonl"  # append-only, auto-written per run
AUTO_WRITE_RESEARCH_LOG = True
