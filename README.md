# MR-v5 Portfolio Backtester & Signal Engine

TradingView cannot backtest this strategy. It evaluates **one symbol at a time**,
and the edge here lives in **cross-sectional ranking** — taking only the 10 deepest
RSI(2) dislocations per day out of 250 candidates. That is why your standalone SBIN
chart showed 126 trades at profit factor 1.149 (≈ no edge) while the portfolio shows
6,753 trades at 1.351. Same rules, different system.

This package is the missing piece.

---

## Quick start

```bash
pip install pandas numpy requests
python backtest.py                  # full backtest, first run downloads ~250 symbols
python backtest.py --symbol sbin    # every trade for one symbol
python backtest.py --rebuild        # refresh matrices after config change
python backtest.py --refresh        # re-download all price data
```

Parameter sweeps — this is how you validate an adjustment:

```bash
python backtest.py --sweep 'COST=0.0025,0.004,0.006'
python backtest.py --sweep 'SLOTS=6,10,15'
python backtest.py --sweep 'MAX_HOLD=5,10,20'
python backtest.py --sweep 'BUY_BELOW=3,5,10'
python backtest.py --sweep 'EXIT_ABOVE=55,65,75'
```

Sweepable names map to `simulate()` kwargs: `slots, cost, max_hold, exit_above,
use_stop, stop_pct, hedge_ratio, compound`.

**Rule for accepting a change:** take a value on a broad *plateau*, never a lone peak.
A lone peak is a curve fit. If neighbouring values collapse, the parameter is noise.

---

## New in this build

- `python backtest.py` now also writes **`out/dashboard.html`**, generated from that
  run's own results — change config, re-run, get a new dashboard.
- `python app.py` serves a web control panel so you can run backtests on Render or
  any container host. See `DEPLOY_AND_LIVE.md`.
- `live/` contains a Dhan-based live trader with dry-run defaults and dual-switch arming.

## Files

| File | Purpose |
|---|---|
| `mrv5/config.py` | **every tunable parameter** — start here |
| `mrv5/data.py` | download + cache NSE daily OHLCV (free, official EOD mirror) |
| `mrv5/engine.py` | indicators, matrix prep, event-driven portfolio simulator |
| `mrv5/report.py` | TradingView-style metrics + monthly table |
| `backtest.py` | runner: full backtest, walk-forward, sweeps, per-symbol |
| `signals.py` | daily signal generator (CLI) |
| `mrv5/dashboard.py` | builds the interactive dashboard from run output |
| `app.py` | Flask service: run backtests + serve dashboard |
| `render.yaml` / `Dockerfile` | deploy configs |
| `live/dhan.py` | DhanHQ v2 adapter |
| `live/state.py` | SQLite position book |
| `live/trader.py` | **live daily loop** — see DEPLOY_AND_LIVE.md |
| `out/trades.csv` | every trade, after each run |
| `out/equity.csv` | daily mark-to-market equity |

---

## Verified baseline

Running as shipped (250 symbols, 2012-06 → 2026-07, 25bps, 10 slots, compounding,
breadth hedge + equity brake):

```
CAGR 30.53%   monthly 2.37%   Sharpe 1.64   Sortino 2.13   MaxDD -21.18%
6,753 trades (477/yr)   win 65.64%   avg hold 4.1d   PF 1.351
IN-SAMPLE  2012-2019: monthly 2.48%  Sharpe 1.85  DD -14.5%
OUT-SAMPLE 2019-2026: monthly 2.23%  Sharpe 1.47  DD -21.2%
```

Reproduce this before changing anything. If your numbers differ, the data changed —
find out why before trusting any adjustment.

---

## Known biases — read before believing the numbers

1. **Survivorship bias (largest issue).** `build_universe()` screens on the *last 750
   days* of turnover, then backtests from 2012. Today's liquidity leaders were
   yesterday's winners, and a dip-buying strategy on pre-selected winners flatters
   itself. Delisted names are absent entirely. **Live results will be lower.**
   *Fix:* rebuild the universe annually from a point-in-time index constituent list.

2. **Costs decide everything.** 25bps → Sharpe 1.64. 40bps → 1.18. 60bps → 0.61.
   At 477 trades/yr, execution quality *is* the strategy. India delivery STT alone
   is ~20bps round-trip. Model your real costs before sizing anything.

3. **The hedge is approximated.** Index futures P&L is simulated from spot returns
   at 1× notional. Real futures carry basis, margin, roll dates, lot rounding.

4. **Risk-layer parameters were chosen on the full sample.** Entry/exit params come
   from published research (untuned, good). The 10%/60-day brake and 40% breadth
   threshold were not. Expect these to behave differently live.

5. **Fills assume you get the open.** 10 market orders at 09:15 on liquid large-caps
   is realistic; on thin names it is not. Prefer limit orders near the open.

---

## Going live: the three-stage path

### Stage 1 — Paper trade (minimum 3 months, non-negotiable)

```bash
python signals.py                          # no open positions
python signals.py sbin infy reliance       # pass current holdings
```

Output:
```
>>> BUY tomorrow at open (top N by lowest RSI2):
    somesymbol       RSI2=1.84   close=432.15
>>> SELL at close: ['infy']
>>> HEDGE: ON — short NIFTY ~1x long notional
```

Run it daily after close. Log the signals, log what you'd have filled, and compare
against the backtest monthly. **The gap between paper and backtest is your real
slippage estimate** — it is almost always worse than modelled.

### Stage 2 — Broker API automation

Indian broker APIs, current landscape:

| Broker | API | Cost | Note |
|---|---|---|---|
| **Zerodha** | Kite Connect | Execution free for personal use; ~₹500/mo per key for data | Largest ecosystem, best docs. Daily browser-login token refresh is the known pain point on a VPS. |
| **Dhan** | DhanHQ | Free | Automation-focused; native TradingView integration |
| **Fyers** | Fyers API | Free | Native TradingView integration |
| **Upstox** | Upstox API | Free | REST + WebSocket |
| **Angel One** | SmartAPI | Free | Widely used |
| **Alice Blue / Shoonya** | ANT / Shoonya | Free | Budget options |

For a **daily** strategy holding 4 days, API latency is irrelevant — pick on cost,
auth ergonomics, and docs. The free APIs (Dhan, Fyers, Upstox, Angel One) remove the
cost barrier entirely; Zerodha wins on ecosystem maturity.

**Compliance:** under SEBI's 2023 retail algo framework, retail algos placing orders
via broker APIs must be **registered and approved through your broker**. Start that
paperwork early — it gates go-live. Verify current requirements with your broker.

Minimal daily loop:

```
16:00  fetch EOD data (broker API or the eod2 mirror)
16:05  python signals.py <current_holdings>   -> JSON
16:10  reconcile: exits to sell, entries to buy, hedge state
09:15  place orders (limit near open, not market)
09:20  verify fills, log everything, alert on mismatch
```

Deploy on a small VPS with cron. Persist positions to SQLite so a crash doesn't
lose state. Log every intended order and every actual fill — reconciliation is
how you catch bugs before they cost money.

### Stage 3 — Scale slowly

Start at 25% of intended capital. Compare live vs paper vs backtest monthly.
Only scale after the live Sharpe holds for two consecutive quarters.

**Kill switches to build in from day one:**
- daily loss > X% → flatten and halt
- live drawdown exceeds backtest max (-21%) → stop, don't "wait for recovery"
- 11 consecutive losses happened in backtest — it will happen live. Decide *now*
  what you'll do, because you won't think clearly in the middle of it.

---

## No-code alternative

If you'd rather not run infrastructure: **Tradetron**, **Streak** (Zerodha), and
**AlgoTest** support strategy automation without code. They handle multi-symbol
scanning, but check whether cross-sectional *ranking* (take only the 10 lowest RSI2)
is expressible — that constraint is the whole strategy, and most no-code builders
only do per-symbol conditions.

---

*Backtested results do not establish future performance. This is not investment advice.*
