"""External daily-data layer: prior-day VIX and the macro-event skip calendar.

Both are second data series the spec depends on but backtesting.py knows nothing
about. The load-bearing correctness property here is NO LOOKAHEAD: the VIX value
a session sees is the prior trading session's close (never same-day), and the
macro skip is a pre-known scheduled calendar matched on the ET session date.
"""
