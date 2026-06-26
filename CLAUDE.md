# ORB Trading System — Project Rules (CLAUDE.md)

This file is the standing contract for the project. `ORB_v1_Phase0_Spec.md` is the strategy source of truth.

## Hard rules — never violate

1. The strategy parameters in `ORB_v1_Phase0_Spec.md` are **FROZEN**. Do not tune, optimize, grid-search, or "improve" any of them (opening-range length, buffer, stop, the five filters, VIX band, volume multiple, notional cap, risk %). If you think one is wrong, **say so and stop** — do not change it.
2. **Paper and backtest only.** Never place, enable, or write code that places a LIVE order unless I explicitly say "go live" in that same message.
3. **Never hardcode API keys or secrets.** Read them from environment variables. Never print, log, or commit them.
4. **The broker (Alpaca) is the source of truth** for positions and orders. Never trust local state over the broker; reconcile against it on startup.
5. **Backtests must use the §6 fill model** (signal-bar close + slippage, stressed at 1×/2×/3×) and must have **no lookahead / repainting**. A backtest that fills at an unrealistic price or peeks at future bars is a failure, not a result.

## Working style

6. **One task per request.** When I ask for a plan, plan only — write no code until I confirm.
7. **Surface assumptions and ambiguities instead of guessing.** If the spec doesn't say, ask.
8. **Minimal, targeted changes.** Do not refactor, rename, reorganize, or touch code outside the current task.
9. **Plain, readable Python** (`backtesting.py` + pandas/numpy). No heavy frameworks or speculative abstractions.
10. After any change, show me **exactly what changed, why, and how to run/verify it.**
