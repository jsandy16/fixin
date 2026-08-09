# Deploying the live dashboard to Render

## What's always-on

`render.yaml` defines two services:

| Service | Type | Purpose |
|---|---|---|
| `mrv5-dashboard` | web | the 3-tab dashboard, always warm |
| `mrv5-eod` | cron | 15:40 IST daily — ingest bars, refresh signals |

Both mount the **same 5 GB disk at `/var/data`**, so `prices.db` and `results.json`
survive redeploys and the cron's work is visible to the web service instantly.

## Plan choice — this matters

**Render's free tier sleeps after 15 minutes of inactivity and has no persistent
disk.** For "anyone clicks the URL and sees everything", free will not do it: the
first visitor after idle waits ~50s for a cold start, and your price DB is gone on
every deploy. `plan: starter` ($7/mo per service) is the minimum for what you asked.

## Steps

1. **Push to GitHub.** Confirm `.env` is *not* in the repo (`git check-ignore .env`).
2. **Render → New → Blueprint**, point at the repo. It reads `render.yaml` and
   creates both services plus the shared disk.
3. **Set secrets** in the Render dashboard (they are `sync: false`, so they are
   never in the repo): `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, `MRV5_EQUITY`.
4. **Seed once.** Render Shell on the web service:
   ```bash
   python bootstrap.py
   ```
   ~30 min: downloads history into `/var/data/prices.db`, runs the backtest, writes
   `results.json`. Re-running is safe — it skips whatever is already done.
5. **Open the URL.** All three tabs render immediately.

## Why the page is instant

The dashboard never runs a simulation on page load. It reads `out/results.json`,
precomputed by `backtest.py` or `POST /api/run`. Measured locally: **19 KB page in
5 ms**. Positions and Portfolio query SQLite directly — also milliseconds. The tabs
auto-refresh every 2 minutes without a reload.

## The three tabs

**Analytics** — toggle between Backtest (in-sample), Forward test (out-of-sample)
and Full period. The split is `SPLIT_DATE` in `mrv5/config.py`, default 2020-01-01.
Each view carries the full TradingView metric set plus alpha, beta, correlation,
VaR/CVaR, Ulcer index, skew, kurtosis, drawdown episodes with recovery time,
breakeven win rate, monthly heatmap, and breakdowns by exit type, year, holding
period and symbol.

**Positions** — every open position with entry date, days held, entry price, latest
mark, % change since entry, unrealised P&L, current RSI(2), days to time stop, and
whether an exit signal has fired. Below it, today's new entry candidates ranked by
deepest RSI(2), flagged `TAKE` or `no slot`.

**Portfolio** — capital, current value, realised vs unrealised split, deployed
capital and utilisation, cash, total return, return per trade, win rate, profit
factor, the equity curve of trades **actually taken**, and full trade history.

Note the Portfolio equity curve is deliberately separate from the Analytics one:
Analytics is simulated, Portfolio is what your account really did. Until the live
or paper trader closes its first position, Portfolio shows an empty-state message
rather than pretending backtest numbers are real.

## Keeping data current

The cron runs `live.ingest --mode daily` then `live.trader --mode eod` at 15:40 IST
(10:10 UTC). Adjust `schedule` if you want a different time — but remember the
settled daily bar only exists after 15:30 IST.

**The cron does not place orders.** It only refreshes data and computes the plan.
Order placement stays on your machine or a separate service you arm deliberately.

## Refreshing the backtest

Either redeploy after running `backtest.py` locally, or `POST /api/run` with
optional overrides:

```bash
curl -X POST https://your-app.onrender.com/api/run \
     -H 'Content-Type: application/json' \
     -d '{"slots": 12, "cost_roundtrip": 0.004}'
```

Poll `/api/status`. This rewrites `results.json` on the shared disk and every
visitor sees the new numbers on next load.

## Security

The dashboard has **no authentication** — anyone with the URL sees your positions
and P&L. That is what you asked for, but be deliberate about it. If you want it
private, put Cloudflare Access in front, or add a token check to the Flask routes.

Your Dhan credentials are only used by the cron service for data. Nothing in the
web service can place an order: `MRV5_ARM` is never set there.
