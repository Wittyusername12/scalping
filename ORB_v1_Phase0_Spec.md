# Phase 0 Spec — ORB v1

**One concrete, frozen, falsifiable strategy hypothesis to take into Phase 1 (backtest).**
Build input for Pine (TradingView) + Python (`backtesting.py`). Nothing here is "tune later" — the core parameters are pre-committed to avoid data-snooping. Version: v1, 2026-06-26.

---

## 0. What this is and the honest prior

This spec defines **one** intraday opening-range-breakout strategy precisely enough to implement and backtest without any further parameter choices. The goal of Phase 1 is **not** to make it look good — it is to find out, for ~$0, whether a disciplined, regime-gated, unleveraged single-ETF ORB clears a realistic cost hurdle.

**The prior is skeptical.** The most rigorous evidence (the 2024 Zarattini/Barbon/Aziz broad-universe result and the independent QuantConnect replication) shows the plain, unfiltered ORB *underperformed*, and profitability appeared only with leverage, leveraged ETFs, or a "stocks in play" news/volume filter — none of which this design uses. So the base-case expectation is that v1 is marginal-to-negative after costs. **A negative Phase 1 result is a success**: it costs nothing and saves real money. We are testing whether the regime/quality filters are enough to push a clean ETF ORB over the line.

---

## 1. Economic rationale (the "why this might work" story)

The opening range encodes the overnight resolution of information and order-flow imbalance. A 15-minute bar that *closes* beyond that range, in the direction the broad market is already leaning (above session VWAP), on above-average volume, is a signature of institutional participation continuing the move rather than a one-tick noise poke. The regime gate (moderate VIX, no scheduled macro shock, a "normal-width" opening range) avoids the chop and fake-gap days where breakouts statistically fail. If an edge exists, it should show up as positive expectancy concentrated on those filtered, trend-aligned, volume-confirmed breakouts — and should survive realistic execution cost.

---

## 2. Instruments

- **Develop & backtest the logic on:** SPY *and* QQQ (deepest liquidity, cleanest history, best-documented behavior). The strategy must work on **both** — a single-symbol result is treated as an artifact, not an edge.
- **Live / whole-share trading set (Phase 4+):** unleveraged, low-priced, liquid ETFs so one whole share is a sane position (brackets require whole shares):
  - **SPYM** (SPDR Portfolio S&P 500, formerly SPLG — renamed Oct 31 2025) — ~$78/share, 0.02% expense, very liquid. **Default live instrument.** Note: its bid-ask spread in % terms is slightly wider than SPY's because of the lower share price — use the upper end of the slippage range when modeling it.
  - **QQQM** (Nasdaq-100, lower-priced QQQ equivalent) — verify current price.
  - **XLF** (~$54), **XLU** (~$45) — liquid sector SPDRs, optional variety. *Verify current prices before sizing.*
- **No leveraged ETFs** (TQQQ/SQQQ/SOXL/etc.). They are cheap per share but carry daily decay and are the fastest route to an intraday-margin-deficit. Explicitly excluded.
- **First end-to-end cycle (backtest -> paper -> micro-live): ONE symbol, LOCKED.** Backtest the logic on **SPY and QQQ** (longest, cleanest history); run the first paper -> micro-live cycle on **SPYM**. This is coherent because SPYM tracks the *same* index as SPY (it is the renamed SPLG, S&P 500) and QQQM tracks the same index as QQQ — their intraday percentage moves are nearly identical, so SPY/QQQ history is a valid proxy for trading SPYM/QQQM; SPYM just has a lower share price for clean whole-share sizing. Add a second symbol only after the first clears every gate.
- SPY/QQQ also serve as **regime reference** even when trading SPYM/QQQM.

---

## 3. Core rules (FROZEN)

| Element | Rule |
|---|---|
| Session | US regular hours only, 9:30-16:00 ET. No pre/post-market. |
| Direction | **Long-only** (v1). Shorts are v2. |
| Opening range (OR) | First 15 minutes, 9:30:00-9:45:00 ET. `OR_high` = highest high, `OR_low` = lowest low of that window. |
| Decision timeframe | 15-minute bars. |
| Entry signal | On the **close** of any 15-min bar after 9:45 that closes **above** `OR_high + buffer`, take the **first** qualifying breakout of the day (and only the first). |
| Buffer | `0.10 x ATR(14)` on the 15-min series, floor of $0.05. |
| Stop (protective) | **Structural:** `OR_low`. Per-share risk = `entry_price - OR_low`. Submitted as the broker-side stop leg. |
| Profit target | **None** in v1. Winners run to the end-of-day exit. |
| Time exit | Hard flatten at **15:50 ET** (before closing-auction effects), enforced by the bridge regardless of the broker leg. |
| Trades per day | **1 per symbol.** No re-entry after a stop-out. |
| Concurrent positions | **1** across the whole system (v1). |
| Entry window | Only 9:45-11:00 ET. No new entries after 11:00. |

---

## 4. Filters (FROZEN regime + quality gates)

All five must pass for an entry to be taken. Each is independently motivated and pre-committed. **We do not sweep these to find the best thresholds** (see §7).

1. **Volume confirmation** — the breakout bar's volume >= `1.3 x SMA(volume, 20)` on the 15-min series.
2. **OR-width filter** — skip the day if OR width (`OR_high - OR_low`) is `< 0.15%` or `> 1.0%` of the OR midpoint price (computed at 9:45). Too narrow -> over-sizing / noise; too wide -> over-risk / the move already happened.
3. **VWAP trend gate** — the breakout bar must close **above** the session VWAP (intraday uptrend confirmation; doubles as the long-direction gate).
4. **Volatility regime gate** — take entries only when **prior-day VIX close is between 12 and 28** (via `request.security`, prior-day value, `lookahead_off`). Dead-calm -> fakeouts; very high -> whipsaw.
5. **Macro-event skip** — skip the **entire session** on scheduled **FOMC announcement, CPI, and NFP (jobs report)** days. Reasoning: CPI and NFP release at 8:30 ET (pre-open), so the 9:45-11:00 entry window sits inside the post-release turbulence and breakouts are just reacting to the data; FOMC statements drop at 2:00 PM ET, so a morning entry is clean but holding into the 2:00 PM move is dangerous given the 15:50 exit — simplest to sit out. ~8 FOMC + 12 CPI + 12 NFP = ~32 days/year (~13% of sessions). *Backtest:* hard-code the date list (all published — Fed calendar for FOMC, BLS for CPI/NFP; compile 2018-present). *Live:* economic-calendar feed or a manual daily check.

> **Discipline note:** more filters = smaller sample = higher overfit risk. Phase 1 must verify (a) the filtered OOS trade count stays >= 100, and (b) no single filter threshold, nudged +/-20%, flips the result from profit to loss. If filtering drops OOS trades below ~100, relax the *least-essential* filter (documented) rather than adding more.

---

## 5. Position sizing (FROZEN)

Whole shares only (bracket compatibility). Computed at entry:

```
risk_dollars      = 0.005 * account_equity            # 0.5% per trade
per_share_risk    = entry_price - stop_price           # = entry - OR_low
raw_shares        = floor(risk_dollars / per_share_risk)
notional_cap      = floor(0.30 * account_equity / entry_price)   # 30% max position
shares            = min(raw_shares, notional_cap)
if shares < 1:  SKIP the trade
```

The **30% notional cap is the real guard**, not the share price: even a ~$78 ETF with a tight stop can demand near-100% of a $4k account, so the cap binds first and you accept *under*-risking when it does. Skipping sub-1-share trades is expected and correct.

---

## 6. Data & backtest methodology

**Data:** split/dividend-adjusted 1-min or 15-min bars for SPY and QQQ, **2018 -> present**. Free sources (Alpaca historical bars API, or TradingView export) are adequate for these liquid names; verify each source's history depth. **Reserve 2024-01-01 -> present as untouched out-of-sample (OOS).** Do not look at it until the gate.

**Two stages:**
1. **TradingView (Pine strategy):** prototype + **Bar Magnifier** on + realistic cost/slippage settings. Use **Bar Replay** to *verify non-repainting* (step bar-by-bar; a confirmed signal must never move or disappear after its bar closes). This is a **coarse filter only** — short history, optimistic fills. It answers "does it fire sensibly and is it non-repainting," not "does it have an edge."
2. **Python (`backtesting.py`):** port the *exact* logic. This is where the gate is decided.

**Fill model (important, architecture-specific):** In live trading the webhook fires at the 15-min bar **close**, the bridge places a marketable-limit within ~1-3 seconds, so the realistic fill ~= **signal-bar close + slippage** — *not* the next-bar open. Model it that way, but keep it honest with an explicit slippage term (this is what prevents the "signal-bar fill fantasy"):
- Slippage (one-way) = half-spread + small impact ~= **1-2 cents/share** on these ETFs; **stress at 1x / 2x / 3x** (up to ~4-6 cents). Use the **upper end for SPYM** (wider % spread). Commission = $0.
- **Pessimistic cross-check:** also run with **next-bar-open** fills (a full bar later). If the edge survives *both* the realistic (close+slippage) and pessimistic (next-bar-open) models, it's robust. If it survives only the realistic model, that's acceptable provided the slippage is realistic and stress-tested — but flag the sensitivity.

**Validation:**
- Walk-forward: ~18-month train / 6-month test, rolled forward.
- Reserved **2024-present OOS**, looked at once.
- **>= 100 OOS trades** after filtering (expected frequency ~1-3 entries/week/symbol).
- **Deflated Sharpe Ratio** (account for the handful of configs tried — keep the research log).
- **Monte-Carlo bootstrap** (10,000 reshuffles of the trade sequence) -> max-drawdown distribution and risk-of-ruin at 0.5% risk on $3-5k.
- **Symbol robustness:** must work on SPY *and* QQQ.
- **Regime breakdown:** report performance by VIX bucket and by calendar year (catch single-regime dependence).

---

## 7. Anti-overfitting commitments

- The following are **FROZEN before backtesting** and **not** optimized: OR length (15m), buffer (0.10xATR), stop (structural `OR_low`), VIX band (12-28), volume multiple (1.3x), notional cap (30%), per-trade risk (0.5%), entry window (9:45-11:00).
- **One** documented re-spec is allowed if v1 fails the gate. Beyond that, shelve ORB and test an alternative (VWAP mean-reversion) rather than mining parameters.
- Keep a **research log** of every backtest run so the Deflated Sharpe can be computed honestly.

---

## 8. Go / No-Go gate (the Phase 1 decision)

**Proceed to Phase 2 (build the bridge) only if ALL hold on the reserved 2024-present OOS:**

- Positive expectancy after **2x modeled slippage** (not just 1x).
- **Profit factor >= 1.10** OOS.
- **>= 100 OOS trades** after filtering.
- Works on **both SPY and QQQ**.
- **Deflated Sharpe meaningfully > 0.**
- **Monte-Carlo 95th-percentile max drawdown < 15%** at the planned sizing.
- **No single filter** threshold, nudged +/-20%, flips profit -> loss.

**If any fail -> do not build the bridge.** Re-spec once (documented) or shelve ORB for the VWAP alternative. A no-go here is the cheap, correct outcome — it's the whole point of doing this for $0 first.

---

## 9. Deliberately excluded (v2+)

Shorts; multiple concurrent positions; profit targets / trailing stops; leveraged ETFs; sub-15-min ranges; additional symbols beyond the first cycle; walk-forward *re-optimization*. v1 stays minimal and falsifiable on purpose.

---

## 10. Decisions (locked 2026-06-26)

1. **Direction:** long-only for v1. Shorts = v2.
2. **Symbols:** backtest on SPY + QQQ; first paper -> live cycle on SPYM (same index as SPY, lower share price). See §2.
3. **Backtest data:** free — Alpaca historical bars API and/or TradingView export. Adequate for these liquid names on 15-min bars; revisit paid data only if moving to finer timeframes.
4. **Macro-event list:** skip the entire session on FOMC + CPI + NFP days; hard-code published dates for the backtest. See §4.

All four are fixed; the spec is build-ready.

## 11. Cost ladder vs phases (when each tier kicks in)

- **Phase 0-1 (spec + backtest): $0.** No live feed needed — backtest on free historical bars; free TradingView account for Pine prototyping + Bar Replay. Meter off for the whole make-or-break phase.
- **Phase 2 (first real-time run, vs paper): pick a path.**
  - *TradingView-as-brain (~$20/mo):* Essential (~$13, the floor for webhooks) + VPS (~$8). Free Cboe feed drives the signal; bridge executes. **Recommended for the first build** — charting/Bar Replay aid debugging, and far less live-infra code in the dangerous real-time path. The spec assumes this path.
  - *Code-as-brain (~$8/mo):* VPS only; signals computed on the VPS off Alpaca's free IEX websocket. Reuses the Phase 1 Python decision logic directly (no Pine), but you build the real-time bar-aggregation/streaming yourself. A later cost/control optimization once the system is proven.
- **+$9.95 TradingView US Stock Markets Bundle:** reactive only, at Phase 3/4, *if* live logs show the free Cboe feed's divergence is costing entries (shouldn't, on 15-min liquid ETFs). Don't buy preemptively.
- **$99 Alpaca SIP:** essentially never for this strategy/size.
