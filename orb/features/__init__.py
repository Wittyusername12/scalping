"""Feature layer: ATR, volume SMA, session VWAP, opening range.

Each feature is a pure, strictly-causal function over the 15-minute series. They
are written to mirror the named TradingView built-ins (Wilder RMA for ATR, HLC3
for VWAP, SMA for volume) so a later Pine port can be checked for parity (I15).
"""
