"""Fill-price math (§6), as pure functions.

Slippage is an absolute cents/share offset applied in the ADVERSE direction on
every fill, both legs: a buy fills higher (+), a sell fills lower (-). The offset
in dollars is ``slippage_cents / 100 * multiplier`` (the 1x/2x/3x stress is the
multiplier).

NOTE ON UNITS: ``slippage_cents`` is in CENTS (e.g. 2.0 = 2c = $0.02). This is
intentionally the function's contract per §6. config.SLIPPAGE_CENTS_PER_SHARE
currently holds a DOLLAR value (0.02), which is inconsistent with this "cents"
contract -- the caller/runner must reconcile (pass cents here, e.g. 2.0). These
functions take slippage as an argument and never read config, so they stay pure.

When fills happen and the realistic-vs-pessimistic toggle are a later runner
concern; this module is price math only.
"""

from __future__ import annotations


def slippage_offset(slippage_cents: float, multiplier: float = 1) -> float:
    """Absolute dollar offset for a one-way fill: cents/100 * multiplier."""
    return (slippage_cents / 100.0) * multiplier


def fill_price(
    reference_price: float,
    side: str,
    slippage_cents: float,
    multiplier: float = 1,
) -> float:
    """Slipped fill price. ``side`` is 'buy' (fills higher) or 'sell' (lower)."""
    offset = slippage_offset(slippage_cents, multiplier)
    if side == "buy":
        return reference_price + offset
    if side == "sell":
        return reference_price - offset
    raise ValueError(f"side must be 'buy' or 'sell', got {side!r}.")


def stop_fill_reference(or_low: float, bar_open: float) -> float:
    """Reference price for a long's protective stop before slippage.

    The stop sits at OR_low, but if a 1-min bar gaps OPEN below OR_low the fill
    is at that worse (lower) open, not the optimistic exact OR_low. So the
    reference is the lower of the two. The sell-side slippage is applied
    separately via fill_price(..., side='sell', ...).
    """
    return min(or_low, bar_open)
