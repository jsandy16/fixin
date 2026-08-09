#!/usr/bin/env python3
"""Price ingestion into the local DB.

    python -m live.ingest --mode seed            # one-time history from the free mirror
    python -m live.ingest --mode daily           # append today's settled bars from Dhan
    python -m live.ingest --mode daily --provisional   # 15:20 snapshot (see warning)
    python -m live.ingest --mode status          # what's in the DB

After seeding once, `daily` fetches only the missing days per symbol, so it is
a few hundred small requests, not a re-download.
"""
import os, sys, time, argparse, datetime
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mrv5 import config as C, data as mirror
from live import db
from live.dhan import Dhan, DhanNotSubscribed, DhanAuthError

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def now_ist(): return datetime.datetime.now(IST)

def last_trading_day(ref=None):
    """Most recent weekday. Does not know NSE holidays — the DB simply won't
    receive a bar on those days, which is handled as 'nothing new'."""
    d = (ref or now_ist()).date()
    while d.weekday() >= 5: d -= datetime.timedelta(days=1)
    return d

def seed(symbols=None, workers=12):
    """Uses the free GitHub mirror — no Dhan subscription needed."""
    """One-time: pull full history from the GitHub mirror into the DB."""
    syms = symbols or mirror.build_universe(C.UNIVERSE_SIZE, C.TURNOVER_LOOKBACK)
    syms = sorted(set(list(syms) + [C.HEDGE_INDEX, 'nifty 50']))
    print(f"seeding {len(syms)} symbols from mirror...")
    total = 0
    for i, s in enumerate(syms, 1):
        d = mirror.load(s)
        if d is None: print(f"  [{i}/{len(syms)}] {s}: no data"); continue
        n = db.upsert(s, d, source='mirror', final=1)
        total += n
        if i % 25 == 0 or i == len(syms):
            print(f"  [{i}/{len(syms)}] {s:<18} +{n:<6} total {total:,}")
    db.log_ingest(last_trading_day(), len(syms), total, 'mirror', 'seed')
    print(f"\nseeded {total:,} bars"); print(db.coverage())

def daily(provisional=False, symbols=None, source='dhan', sleep=0.25):
    """Append missing bars. Default source is Dhan (your broker = authoritative)."""
    asof = last_trading_day()
    syms = symbols or db.symbols() or mirror.build_universe(C.UNIVERSE_SIZE, C.TURNOVER_LOOKBACK)
    need = db.stale(syms, asof)
    print(f"as-of {asof}  |  {len(syms)} tracked  |  {len(need)} need updating")
    if not need:
        print("database already current — nothing to do"); return 0

    t = now_ist()
    if not provisional and t.hour < 15 or (t.hour == 15 and t.minute < 35):
        print(f"\n  NOTE: it is {t:%H:%M} IST. The settled daily bar is only available")
        print( "  after 15:30. Run after 15:40, or pass --provisional to use a")
        print( "  partial bar (which can flip an RSI(2) signal before the close).\n")

    dh = Dhan(dry_run=True) if source == 'dhan' else None
    frm = asof - datetime.timedelta(days=20)
    total = ok = fail = 0
    for i, s in enumerate(need, 1):
        try:
            if source == 'dhan':
                d = dh.daily(s, frm, asof)
            else:
                d = mirror.load(s, refresh=True)
                if d is not None: d = d[d.index >= pd.Timestamp(frm)]
            if d is None or len(d) == 0: fail += 1; continue
            last = db.last_date(s)
            if last: d = d[d.index > pd.Timestamp(last)]
            if len(d) == 0: continue
            n = db.upsert(s, d, source=source, final=0 if provisional else 1)
            total += n; ok += 1
            if i % 25 == 0: print(f"  [{i}/{len(need)}] +{total} bars")
            if sleep: time.sleep(sleep)
        except (DhanNotSubscribed, DhanAuthError) as e:
            print(f"\n  ABORTED after {i} symbols — this affects every request:\n")
            print(f"  {e}\n")
            print(f"  Meanwhile you can run entirely on free data:")
            print(f"      python -m live.ingest --mode seed")
            print(f"      python -m live.ingest --mode daily --source mirror")
            db.log_ingest(asof, ok, total, source, f'aborted: {type(e).__name__}')
            return total
        except KeyError as e:
            fail += 1
            if fail <= 3: print(f"  {s}: {e}")
            if fail == 3: print("  ... (further symbol-resolution errors suppressed; "
                                "run `python -m live.ingest --mode scrip` to fix)")
        except Exception as e:
            fail += 1
            if fail <= 5: print(f"  {s}: {e}")
    db.log_ingest(asof, ok, total, source, 'provisional' if provisional else 'settled')
    print(f"\ningested {total} bars across {ok} symbols ({fail} failed)")
    print(db.coverage())
    return total

def status():
    cov = db.coverage()
    print(f"DB: {db.DB_PATH}")
    print(f"  symbols      {cov['syms']}")
    print(f"  rows         {cov['rows']:,}")
    print(f"  range        {cov['mn']} -> {cov['mx']}")
    print(f"  provisional  {cov['provisional']}")
    asof = last_trading_day()
    if not cov['mx']:
        print(f"  last trading day {asof} -> EMPTY (run: python -m live.ingest --mode seed)")
    else:
        fresh = str(cov['mx']) >= str(asof)
        lag = (asof - datetime.date.fromisoformat(cov['mx'])).days
        print(f"  last trading day {asof} -> "
              f"{'CURRENT' if fresh else f'STALE by {lag} day(s)'}")
    c = db.conn()
    print("\n  recent ingests:")
    for r in c.execute("SELECT * FROM ingest_log ORDER BY ts DESC LIMIT 8"):
        print(f"    {r['ts']}  {r['trade_date']}  {r['symbols']:>4} syms  "
              f"{r['rows']:>7} rows  {r['source']:<7} {r['note']}")
    c.close()

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['seed','daily','status','scrip'], required=True)
    ap.add_argument('--provisional', action='store_true', help='use pre-close partial bar')
    ap.add_argument('--source', choices=['dhan','mirror'], default='dhan')
    ap.add_argument('--symbols', default=None, help='comma list, else full universe')
    a = ap.parse_args()
    syms = a.symbols.split(',') if a.symbols else None
    if a.mode == 'scrip':
        Dhan(dry_run=True).diagnose_scrip()
    elif a.mode == 'seed':  seed(syms)
    elif a.mode == 'daily': daily(a.provisional, syms, a.source)
    else:                   status()
