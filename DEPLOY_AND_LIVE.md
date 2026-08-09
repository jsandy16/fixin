# Deploy + Live Trading Guide

## 1. Dashboard is now generated from each run

`mrv5/dashboard.py` builds the HTML **from the equity Series and trades DataFrame
your run produced**. Nothing is hard-coded. Change a parameter, re-run, and the
dashboard regenerates — different numbers, different charts, different config panel.

```bash
python backtest.py            # -> out/equity.csv, out/trades.csv, out/dashboard.html
```

Verified: `slots=10 hold=10 cost=25bps` → Sharpe 1.64, 6,753 trades.
`slots=6 hold=20 cost=40bps` → Sharpe 1.41, 4,334 trades. Two runs, two dashboards.

Tabs: Overview (equity vs benchmark, drawdown, yearly), Performance, Monthly heatmap,
Exits, Symbols (filterable), Trades (filterable), Config. The Config tab records the
exact parameters that produced the page, so results are never orphaned from settings.

---

## 2. Run it anywhere

```bash
python app.py                 # local, http://localhost:8000
```

| Route | Purpose |
|---|---|
| `GET /` | control panel — set params, click run, watch the log |
| `POST /run` | start a backtest, JSON body overrides config |
| `GET /status` | job state + live log tail |
| `GET /dashboard` | latest generated dashboard |
| `GET /signals?holdings=sbin,infy` | today's live signals as JSON |
| `GET /download/trades.csv` | raw output |
| `GET /health` | uptime probe |

**Render.com:** push to GitHub → New Web Service → it reads `render.yaml`.
Two things matter there: `--timeout 3600` (a full backtest takes minutes and the
default 30s gunicorn timeout will kill it), and the 2 GB disk mounted at `cache/`
so you don't re-download 250 symbols on every deploy. The free tier sleeps and
wipes disk — use Starter if you want the cache to persist.

**Anywhere else:** the `Dockerfile` works on Railway, Fly.io, Cloud Run, or your own VPS.

Tested locally: `/health` 200, panel 200, dashboard 200 (175 KB), CSV downloads 200,
path-traversal on `/download` blocked.

---

## 3. Live trading on Dhan

```
live/dhan.py      DhanHQ v2 REST adapter (orders, positions, funds, historical)
live/state.py     SQLite book — survives crashes and restarts
live/trader.py    the daily loop
live/symbol_map.json   symbol -> securityId overrides
```

### Daily cycle

```bash
# after close (~16:00) — compute tomorrow's plan
python -m live.trader --mode eod

# at 09:15 — execute
python -m live.trader --mode open                    # DRY RUN
MRV5_ARM=YES python -m live.trader --mode open --live # REAL ORDERS

# ~09:25 — reconcile broker against local book
MRV5_ARM=YES python -m live.trader --mode status --live
```

cron:
```
0 16 * * 1-5  cd /opt/mrv5 && python -m live.trader --mode eod   >> logs/eod.log 2>&1
15 9 * * 1-5  cd /opt/mrv5 && MRV5_ARM=YES python -m live.trader --mode open --live >> logs/open.log 2>&1
25 9 * * 1-5  cd /opt/mrv5 && MRV5_ARM=YES python -m live.trader --mode status --live >> logs/status.log 2>&1
```

### Setup

```bash
export DHAN_CLIENT_ID=your_client_id
export DHAN_ACCESS_TOKEN=your_jwt
export MRV5_EQUITY=1000000       # real capital — drives position sizing
export MRV5_MAX_ORDER=200000     # per-order value cap
export MRV5_MAX_ORDERS=25        # daily order count cap
```

`--source dhan` pulls OHLC from your Dhan account instead of the free mirror.
Use it once you're live so backtest and execution share one data source.

If Dhan's scrip-master layout changes, the adapter says so plainly and falls back
to `live/symbol_map.json`. Populate that file from
`https://images.dhan.co/api-data/api-scrip-master-detailed.csv` if you want to
skip the download entirely.

### Safety design

- **Two independent switches for live orders**: `--live` *and* `MRV5_ARM=YES`.
  One is too easy to leave on. Verified: only both together arm it.
- **LIMIT orders by default** at +0.3% for buys, −0.3% for sells. Market orders at
  the open across a 250-name universe is precisely how backtest edge disappears.
- **Order value + daily count caps**, checked before anything is sent.
- **Exits execute before entries** — frees slots and cash first.
- **SQLite journal** of every intended order, every fill, every error.
- **Live mode refuses to start without credentials** rather than silently dry-running.
- **The hedge is deliberately NOT automated.** Shorting NIFTY futures is left as a
  manual step the plan reminds you about. Automating a futures leg with margin and
  roll handling is a bigger job than it looks, and getting it wrong is expensive.

### Before you send a real order

1. **Paper trade 3 months minimum.** Run `--mode eod` daily, log the plan, log what
   you'd have filled, compare monthly against the backtest. That gap is your real
   slippage — and it is always worse than modelled. Costs decide this strategy:
   25bps → Sharpe 1.64, 40bps → 1.18, 60bps → 0.61.
2. **Register your algo with Dhan.** Under SEBI's retail algo framework, API-placed
   algo orders must be registered and exchange-approved through your broker. This
   gates go-live — start it early.
3. **Start at 25% of intended capital.** Scale only after live Sharpe holds two quarters.
4. **Decide your kill switches now**: daily loss cap, and a hard stop if live drawdown
   exceeds the backtest max. The backtest had **11 consecutive losses**; it will happen
   live, and you will not think clearly in the middle of it.

---

*Backtested results do not establish future performance. Not investment advice.*
