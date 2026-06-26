"""Entry signal: the breakout trigger and the five §4 filters.

Every function is pure: it takes precomputed scalar inputs (already computed on
the 15-min series elsewhere) and returns a value. Numeric thresholds come from
orb/config.py; operators (strict > vs >=, inclusive bands) are pinned to the spec
and exercised at the exact boundary by the tests.

Filter split (per I4 / the runner's use):
  - session-level (known at 09:45, rules a whole day out once): OR-width, VIX, macro.
  - bar-level (re-checked on each candidate breakout bar): volume, VWAP.
The composer reports every filter individually regardless of the split.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable, NamedTuple

from orb import config


# --- breakout trigger (§3) ------------------------------------------------

def entry_buffer(atr: float) -> float:
    """Breakout buffer = 0.10 * ATR(14), with a $0.05 minimum (a floor)."""
    return max(config.BUFFER_ATR_MULT * atr, config.BUFFER_FLOOR_DOLLARS)


def breakout_triggered(close: float, or_high: float, buffer: float) -> bool:
    """A bar breaks out when its close is STRICTLY above OR_high + buffer (M1)."""
    return close > or_high + buffer


# --- the five filters (§4) ------------------------------------------------

def filter_volume(volume: float, volume_sma: float) -> bool:
    """§4.1 -- breakout-bar volume >= 1.3 * SMA(volume, 20). Inclusive (>=).

    volume_sma is expected to EXCLUDE the breakout bar (handled upstream, B8).
    """
    return volume >= config.VOLUME_MULTIPLE * volume_sma


def filter_or_width(or_high: float, or_low: float, or_mid: float) -> bool:
    """§4.2 -- OR width as a percent of the OR midpoint within [0.15%, 1.0%].

    Inclusive at both ends. config stores the band in PERCENT (0.15, 1.0), so
    the width ratio is multiplied by 100. (width*100/mid order is used so the
    boundary arithmetic stays exactly representable.)
    """
    width_pct = (or_high - or_low) * 100.0 / or_mid
    return config.OR_WIDTH_MIN_PCT <= width_pct <= config.OR_WIDTH_MAX_PCT


def filter_vwap(close: float, vwap: float) -> bool:
    """§4.3 -- breakout bar must close STRICTLY above session VWAP."""
    return close > vwap


def filter_vix(prior_day_vix: float) -> bool:
    """§4.4 -- prior-day VIX close within [12, 28], inclusive both ends."""
    return config.VIX_MIN <= prior_day_vix <= config.VIX_MAX


def filter_macro(session_date: dt.date, skip_dates: Iterable[dt.date]) -> bool:
    """§4.5 -- True (tradeable) unless the session date is a macro-skip day."""
    return session_date not in skip_dates


# --- composers ------------------------------------------------------------

class SessionGate(NamedTuple):
    """Day-level filters, evaluable once at 09:45 to rule a whole day out."""
    or_width_ok: bool
    vix_ok: bool
    macro_ok: bool
    day_eligible: bool


class EntryDecision(NamedTuple):
    """Per-filter results for a candidate breakout bar plus the overall verdict."""
    breakout: bool
    volume_ok: bool
    or_width_ok: bool
    vwap_ok: bool
    vix_ok: bool
    macro_ok: bool
    session_ok: bool   # all three session-level filters pass
    enter: bool        # breakout AND all five filters


def evaluate_session(
    *,
    or_high: float,
    or_low: float,
    or_mid: float,
    prior_day_vix: float,
    session_date: dt.date,
    skip_dates: Iterable[dt.date],
) -> SessionGate:
    """Evaluate the three session-level filters (OR-width, VIX, macro)."""
    or_width_ok = filter_or_width(or_high, or_low, or_mid)
    vix_ok = filter_vix(prior_day_vix)
    macro_ok = filter_macro(session_date, skip_dates)
    return SessionGate(
        or_width_ok=or_width_ok,
        vix_ok=vix_ok,
        macro_ok=macro_ok,
        day_eligible=or_width_ok and vix_ok and macro_ok,
    )


def evaluate_entry(
    *,
    close: float,
    or_high: float,
    or_low: float,
    or_mid: float,
    atr: float,
    volume: float,
    volume_sma: float,
    vwap: float,
    prior_day_vix: float,
    session_date: dt.date,
    skip_dates: Iterable[dt.date],
) -> EntryDecision:
    """Full entry decision for one candidate breakout bar.

    Reports each of the five filters and the breakout individually, plus the
    overall ``enter`` (breakout AND every filter). The session-level subset is
    summarized by ``session_ok`` so the runner can short-circuit a whole day.
    """
    gate = evaluate_session(
        or_high=or_high,
        or_low=or_low,
        or_mid=or_mid,
        prior_day_vix=prior_day_vix,
        session_date=session_date,
        skip_dates=skip_dates,
    )

    buffer = entry_buffer(atr)
    breakout = breakout_triggered(close, or_high, buffer)
    volume_ok = filter_volume(volume, volume_sma)
    vwap_ok = filter_vwap(close, vwap)

    enter = breakout and volume_ok and vwap_ok and gate.day_eligible

    return EntryDecision(
        breakout=breakout,
        volume_ok=volume_ok,
        or_width_ok=gate.or_width_ok,
        vwap_ok=vwap_ok,
        vix_ok=gate.vix_ok,
        macro_ok=gate.macro_ok,
        session_ok=gate.day_eligible,
        enter=enter,
    )
