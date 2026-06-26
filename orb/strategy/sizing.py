"""Position sizing -- the §5 whole-share formula, as a pure function.

    risk_dollars   = RISK_PER_TRADE * account_equity      # 0.5%
    per_share_risk = entry_price - stop_price             # = entry - OR_low
    raw            = floor(risk_dollars / per_share_risk)
    cap            = floor(NOTIONAL_CAP_PCT * account_equity / entry_price)  # 30%
    shares         = min(raw, cap);  if shares < 1 -> 0

Whole shares only. The 30% notional cap is the real guard (no minimum-stop
parameter, per M4). Price-agnostic: it sizes whatever entry_price it is given;
live-price scaling happens upstream (I11).
"""

from __future__ import annotations

import math

from orb import config


def position_size(account_equity: float, entry_price: float, stop_price: float) -> int:
    """Whole-share size per §5. Returns 0 when the trade should be skipped.

    Raises ValueError if per_share_risk = entry_price - stop_price is not
    strictly positive: for a long, the entry must be above the stop (OR_low).
    A non-positive value is a programming error upstream, not something to size
    silently (M4).
    """
    per_share_risk = entry_price - stop_price
    if per_share_risk <= 0:
        raise ValueError(
            f"per_share_risk must be > 0 (entry {entry_price} must exceed "
            f"stop {stop_price}); got {per_share_risk}."
        )

    risk_dollars = config.RISK_PER_TRADE * account_equity
    raw = math.floor(risk_dollars / per_share_risk)
    cap = math.floor(config.NOTIONAL_CAP_PCT * account_equity / entry_price)
    shares = min(raw, cap)
    return shares if shares >= 1 else 0
