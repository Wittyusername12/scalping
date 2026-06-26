"""Strategy decision logic: pure functions only.

signals.py  -- the breakout trigger and the five §4 filters, plus a composer.
sizing.py   -- the §5 whole-share position-size formula.
fills.py    -- the §6 fill-price math (cents/share slippage; gap-through stop).

Nothing here touches pandas, backtesting.py, or any I/O. Every numeric threshold
is read from orb/config.py; none is redefined or hardcoded here.
"""
