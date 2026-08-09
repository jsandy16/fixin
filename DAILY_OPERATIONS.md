# Daily Operations — local DB, live trading, backtesting

## What you need from Dhan

| Item | Where to get it | Notes |
|---|---|---|
| **Client ID** | Dhan web → Profile → DhanHQ Trading APIs | short numeric string |
| **Access Token** | same page → Generate Access Token | JWT, **expires in 30 days** — diarise the renewal |
| **Data API subscription** | ₹499/month (as of writing — confirm current) | needed for `/charts/historical` |
| **Algo registration** | raise with Dhan support | SEBI requires API algo orders to be registered + exchange-approved. **Start this now — it gates go-live.** |

### Where credentials go

```bash
cp .env.example .env      # then edit .env
python -m live.trader --mode check
```

`.env` sits in the project root, is loaded automatically by every module, and is
git-ignored. `--mode check` prints what's configured (token masked), then makes a
real call to `/fundlimit` to confirm the credentials work.

```
DHAN_CLIENT_ID=1100123456
DHAN_ACCESS_TOKEN=eyJ0eXAiOiJKV1Qi...
MRV5_EQUITY=1000000        # real capital; drives position sizing
MRV5_MAX_ORDER=200000      # per-order value cap
MRV5_MAX_ORDERS=25         # daily order count cap
# MRV5_ARM=YES             # leave commented while paper trading
```

Real environment variables **override** `.env`, so a systemd unit or CI secret can
take precedence without editing the file:

```ini
# /etc/systemd/system/mrv5-eod.service
[Service]
WorkingDirectory=/opt/mrv5
EnvironmentFile=/opt/mrv5/.env
ExecStart=/usr/bin/python3 -m live.trader --mode eod
```

Never commit `.env`. If you push it by accident, rotate the token in Dhan immediately.

---

## Troubleshooting

### `DH-902 — User has not subscribed to Data APIs`

Your Dhan account lacks the **Data API** add-on (~₹499/month, separate from the
Trading API). Two options:

**Option A — don't buy it.** The free GitHub EOD mirror carries the same NSE
official data. Everything works:

```bash
python -m live.ingest --mode seed                     # free
python -m live.ingest --mode daily --source mirror    # free, run daily
python -m live.trader --mode eod                      # signals
```

You only need Dhan for **placing orders**, which is the Trading API, not Data.
The mirror publishes with a lag, so the freshness guard in `--mode eod` matters —
it will abort rather than trade on stale bars.

**Option B — subscribe.** Dhan web → Profile → DhanHQ APIs → subscribe to Data
APIs. Then `--source dhan` works and your data matches your execution venue
exactly, which is the better end state once you are live.

### `no Dhan securityId for 'icicibank'`

The scrip master failed to load, so only the handful in `live/symbol_map.json`
resolved. Diagnose it:

```bash
python -m live.ingest --mode scrip
```

That downloads the master, prints the columns it found, and probes five symbols.
If the download itself fails, download the CSV manually to `live/scrip_master.csv`:

```
https://images.dhan.co/api-data/api-scrip-master-detailed.csv
```

### `DH-901 / Invalid token`

Access tokens expire every 30 days. Regenerate in the Dhan dashboard and update
`.env`. Verify with `python -m live.trader --mode check`.

---

## One-time setup

```bash
pip install -r requirements.txt
python -m live.ingest --mode seed        # ~20-40 min: full history -> data/prices.db
python -m live.ingest --mode status      # confirm it landed
```

**Run `seed` first.** It needs no Dhan subscription — it pulls from the free
mirror. If you skip it, `--mode daily` has an empty DB and tries to fetch 250
symbols in one go.

```bash
```

Seeding pulls history from the free GitHub mirror because backfilling 250 symbols ×
14 years through Dhan would take hours and burn rate limit. From then on **Dhan is
the source of truth** for every new bar.

---

## Every trading day

```bash
# 15:40 IST — after the settled close
python -m live.ingest --mode daily              # append today's bars from Dhan
python -m live.trader --mode eod                # -> live/plan.json

# 09:15 next morning
python -m live.trader --mode open --paper                      # paper
MRV5_ARM=YES python -m live.trader --mode open --live          # real orders

# 09:25 — reconcile
MRV5_ARM=YES python -m live.trader --mode status --live
```

crontab:
```cron
40 15 * * 1-5  cd /opt/mrv5 && python -m live.ingest --mode daily  >> logs/ingest.log 2>&1
45 15 * * 1-5  cd /opt/mrv5 && python -m live.trader --mode eod    >> logs/eod.log 2>&1
15  9 * * 1-5  cd /opt/mrv5 && MRV5_ARM=YES python -m live.trader --mode open --live >> logs/open.log 2>&1
25  9 * * 1-5  cd /opt/mrv5 && MRV5_ARM=YES python -m live.trader --mode status --live >> logs/status.log 2>&1
```

Drop `MRV5_ARM=YES` and add `--paper` to run the whole thing as paper trading.

### About running at 15:20

You asked for 15:20. **The daily bar is not settled until 15:30**, so a 15:20 pull
gives a partial bar — and RSI(2) is the most close-sensitive indicator there is.
A stock at RSI(2)=4.8 at 15:20 can finish at 6.2 and the signal vanishes.

The DB handles this correctly if you insist: `--provisional` marks the bar `final=0`,
and the next settled ingest overwrites it. Provisional never overwrites settled.

But consider: **orders are placed at tomorrow's open either way.** Running at 15:40
instead of 15:20 costs you nothing and removes the problem entirely. I'd only use
`--provisional` if you have a same-day reason.

---

## Backtesting — no re-extraction

Once the DB exists, everything reads from it:

```bash
python backtest.py            # reads data/prices.db, no downloads
```

Verified: with the mirror fallback **deliberately disabled**, `data.load()`,
`build_universe()` and a full 60-symbol / 3,504-day / 2,898-trade backtest all ran
from SQLite alone in seconds. Set `MRV5_USE_DB=0` to force the mirror instead.

Your DB grows every day, so your backtest window extends automatically — and by
construction it contains exactly the data your live system traded on.

---

## Safety

- **Live orders need three conditions**: `--live` AND `MRV5_ARM=YES` AND no `--paper`.
- **Freshness guard**: `--mode eod` aborts if the newest bar predates the last
  trading day. This is the failure that is otherwise silent — every RSI(2) one day
  stale, entering setups the market already left. Override with `--allow-stale`
  only when you know why.
- **LIMIT orders** at ±0.3%, CNC delivery, value and count caps enforced pre-send.
- **Exits before entries**; SQLite journal of every intended order and fill.
- **Hedge is not automated** — the plan reminds you; you place the futures leg.

---

## Verified vs unverified

**Tested (30/30 checks, `python tests/test_live_flow.py`)** — Dhan response parsing,
DB round-trip and idempotency, provisional/settled precedence, signal generation off
DB data, freshness guard abort, order construction (LIMIT/CNC/qty/price/securityId),
all four arming permutations, account endpoints.

**NOT tested — `api.dhan.co` is unreachable from my environment.** The mock replays
Dhan's *documented* response shape; if the live API differs in field names or types,
`daily()` will need adjusting. Before trusting anything:

```bash
python -m live.ingest --mode daily --symbols sbin
python -m live.ingest --mode status
```

If SBIN's bar lands with a sane date and OHLC, the contract matches. If it doesn't,
send me the error and I'll fix the parser.
