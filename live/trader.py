#!/usr/bin/env python3
"""LIVE TRADER — MR-v5 daily loop on Dhan.

    python -m live.trader --mode eod     # after close: compute tomorrow's plan
    python -m live.trader --mode paper   # next morning: apply the plan to the PAPER book
    python -m live.trader --mode open    # at 09:15: execute the plan (REAL orders)
    python -m live.trader --mode status  # reconcile broker vs local book

SAFETY: dry-run is the DEFAULT. Live orders require BOTH:
    --live  on the command line   AND   MRV5_ARM=YES  in the environment.
Two independent switches, because one is too easy to leave on.
"""
import os, sys, json, argparse, datetime
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from mrv5 import env as _env  # loads .env into os.environ

import pandas as pd, numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mrv5 import config as C, data, engine
from live.dhan import Dhan, DhanNotSubscribed, DhanAuthError
from live import state

# ---------------- risk rails ----------------
MAX_ORDER_VALUE   = float(os.environ.get('MRV5_MAX_ORDER', 200_000))
MAX_DAILY_ORDERS  = int(os.environ.get('MRV5_MAX_ORDERS', 25))
MAX_POSITIONS     = C.SLOTS
LIMIT_OFFSET_PCT  = 0.003     # buy 0.3% above ref price so limits actually fill
PLAN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plan.json')

def armed(args):
    """Live orders need BOTH switches and no --paper override."""
    if getattr(args, 'paper', False): return False
    return args.live and os.environ.get('MRV5_ARM') == 'YES'

# ---------------- data ----------------
def price_history(dh, symbols, days=420, source='db'):
    """source: 'db' = local SQLite (default; populated by live.ingest)
               'dhan' = live pull from Dhan
               'mirror' = free GitHub EOD mirror"""
    from live import db as pricedb
    out = {}
    if source == 'db':
        for s in symbols:
            d = pricedb.load(s)
            if d is not None and len(d) >= C.MIN_HISTORY: out[s] = d
    elif source == 'dhan':
        to = datetime.date.today(); frm = to - datetime.timedelta(days=int(days*1.6))
        for s in symbols:
            try:
                d = dh.daily(s, frm, to)
                if d is not None and len(d) >= C.MIN_HISTORY: out[s] = d
            except Exception as e:
                print(f"  skip {s}: {e}")
    else:
        for s in symbols:
            d = data.load(s, refresh=True)
            if d is not None and len(d) >= C.MIN_HISTORY: out[s] = d
    return out

def assert_fresh(px, require=True):
    """Refuse to trade on stale data. This is the failure that is otherwise silent:
    every RSI(2) one day old, entering setups the market already moved past."""
    from live.ingest import last_trading_day
    asof = str(last_trading_day())
    latest = max((str(d.index[-1].date()) for d in px.values()), default='0000')
    fresh = latest >= asof
    print(f"data freshness: latest bar {latest}, last trading day {asof} -> "
          f"{'OK' if fresh else 'STALE'}")
    if not fresh and require:
        raise SystemExit(
            f"ABORT: newest bar is {latest} but the last trading day was {asof}.\n"
            f"Run:  python -m live.ingest --mode daily\n"
            f"Override with --allow-stale only if you know why.")
    return fresh

# ---------------- planning ----------------
def _load_symbol(dh, s, source):
    """Load one symbol's history for the plan. Streaming (one at a time) keeps
    build_plan memory-light enough to run on the 512Mi web instance."""
    from live import db as pricedb
    if source == 'db':
        d = pricedb.load(s)
    elif source == 'dhan':
        to = datetime.date.today(); frm = to - datetime.timedelta(days=672)
        d = dh.daily(s, frm, to)
    else:
        d = data.load(s, refresh=True)
    return d if (d is not None and len(d) >= C.MIN_HISTORY) else None

def build_plan(source='db', allow_stale=False):
    """Stream the universe one symbol at a time (no dict of all 252 frames) so
    this fits in the free instance's memory. Same plan output as before."""
    from live.ingest import last_trading_day
    dh = Dhan(dry_run=True)
    held = {p['symbol']: p for p in state.open_positions()}
    universe = data.build_universe(C.UNIVERSE_SIZE, C.TURNOVER_LOOKBACK)
    need = sorted(set(universe) | set(held))
    print(f"open positions: {len(held)} {list(held)}")
    print(f"scanning {len(need)} symbols ({source})...")

    exits, entries = [], []
    n_above = n_have = 0
    latest = '0000-00-00'
    for s in need:
        try:
            d = _load_symbol(dh, s, source)
        except (DhanNotSubscribed, DhanAuthError):
            raise
        except Exception:
            d = None
        if d is None:
            continue
        n_have += 1
        latest = max(latest, str(d.index[-1].date()))
        if len(d) > 200 and d.Close.iloc[-1] > d.Close.rolling(200).mean().iloc[-1]:
            n_above += 1
        p = engine.prep(d)
        if p is None or len(p) < 2:
            continue
        last = p.iloc[-1]
        if s in held:
            age = (pd.Timestamp.today().normalize() - pd.Timestamp(held[s]['entry_date'])).days
            reason = None
            if last.rsi2 > C.EXIT_ABOVE:        reason = 'rsi'
            elif last.Close > last.smaX:        reason = 'sma5'
            elif age >= C.MAX_HOLD_DAYS:        reason = 'time'
            if reason:
                exits.append(dict(symbol=s, qty=held[s]['qty'], reason=reason,
                                  ref_price=float(last.Close)))
        elif bool(last.signal):
            entries.append(dict(symbol=s, rsi2=float(last.rsi2), ref_price=float(last.Close)))

    if not n_have:
        raise SystemExit("no price data. Run: python -m live.ingest --mode seed")
    asof = str(last_trading_day())
    fresh = latest >= asof
    print(f"data freshness: latest bar {latest}, last trading day {asof} -> {'OK' if fresh else 'STALE'}")
    if not fresh and not allow_stale:
        raise SystemExit(f"ABORT: newest bar {latest} < last trading day {asof}. "
                         f"Run ingest, or pass --allow-stale.")
    entries.sort(key=lambda x: x['rsi2'])

    # regime / hedge
    try:
        idx = _load_symbol(dh, C.HEDGE_INDEX, source)
    except Exception:
        idx = None
    if idx is None:
        idx = data.load(C.HEDGE_INDEX)
    below = bool(idx.Close.iloc[-1] < idx.Close.rolling(C.HEDGE_INDEX_SMA).mean().iloc[-1]) if idx is not None else False
    breadth = n_above / max(n_have, 1)
    hedge = bool(below or breadth < C.HEDGE_BREADTH_MIN)

    free = MAX_POSITIONS - (len(held) - len(exits))
    take = entries[:max(free, 0)]
    equity = float(os.environ.get('MRV5_EQUITY', C.CAPITAL))
    slot = equity / C.SLOTS
    for e in take:
        e['qty'] = int(min(slot, MAX_ORDER_VALUE) / e['ref_price'])
        e['limit'] = round(e['ref_price'] * (1 + LIMIT_OFFSET_PCT), 1)
    take = [e for e in take if e['qty'] >= 1]

    plan = dict(generated=str(datetime.datetime.now())[:19],
                for_session=str((datetime.date.today() + datetime.timedelta(days=1))),
                exits=exits, entries=take, hedge_required=hedge,
                breadth=round(breadth, 3), index_below_sma=below,
                open_positions=len(held), free_slots=free, equity=equity)
    json.dump(plan, open(PLAN_PATH, 'w'), indent=2)
    state.save_run(plan)
    return plan

def show(plan):
    print(f"\n{'='*64}\nPLAN for {plan['for_session']}   (generated {plan['generated']})\n{'='*64}")
    print(f"  open {plan['open_positions']}  free slots {plan['free_slots']}  "
          f"breadth {plan['breadth']}  hedge {'ON' if plan['hedge_required'] else 'off'}")
    print(f"\n  SELL at open ({len(plan['exits'])}):")
    for e in plan['exits']: print(f"    {e['symbol']:<16} qty {e['qty']:<6} [{e['reason']}]")
    print(f"\n  BUY at open ({len(plan['entries'])}):")
    for e in plan['entries']:
        print(f"    {e['symbol']:<16} qty {e['qty']:<6} limit {e['limit']:<10} RSI2 {e['rsi2']:.1f}")
    if plan['hedge_required']:
        print("\n  HEDGE: short NIFTY futures ~1x long notional  [MANUAL — not automated]")

# ---------------- execution ----------------
def execute(args):
    if not os.path.exists(PLAN_PATH):
        print("no plan.json — run --mode eod first"); return
    plan = json.load(open(PLAN_PATH))
    live = armed(args)
    print(f"\n*** {'LIVE — REAL ORDERS' if live else 'DRY RUN — no orders sent'} ***\n")
    dh = Dhan(dry_run=not live)

    n = len(plan['exits']) + len(plan['entries'])
    if n > MAX_DAILY_ORDERS:
        print(f"ABORT: {n} orders exceeds MAX_DAILY_ORDERS={MAX_DAILY_ORDERS}"); return

    # exits first — frees slots and cash
    for e in plan['exits']:
        try:
            lim = round(e['ref_price'] * (1 - LIMIT_OFFSET_PCT), 1)
            r = dh.place(e['symbol'], 'SELL', e['qty'], 'LIMIT', lim, tag=f"x{e['symbol'][:8]}")
            oid = r.get('orderId', 'DRY')
            print(f"  SELL {e['symbol']:<16} qty {e['qty']:<6} @ {lim:<9} -> {oid}")
            state.log('EXIT', e['symbol'], 'SELL', e['qty'], lim, oid, e['reason'])
            if live: state.close_position(e['symbol'])
        except Exception as ex:
            print(f"  FAILED SELL {e['symbol']}: {ex}"); state.log('ERROR', e['symbol'], note=str(ex))

    for e in plan['entries']:
        val = e['qty'] * e['limit']
        if val > MAX_ORDER_VALUE:
            print(f"  SKIP {e['symbol']}: order value {val:,.0f} > cap {MAX_ORDER_VALUE:,.0f}"); continue
        try:
            r = dh.place(e['symbol'], 'BUY', e['qty'], 'LIMIT', e['limit'], tag=f"e{e['symbol'][:8]}")
            oid = r.get('orderId', 'DRY')
            print(f"  BUY  {e['symbol']:<16} qty {e['qty']:<6} @ {e['limit']:<9} -> {oid}")
            state.log('ENTRY', e['symbol'], 'BUY', e['qty'], e['limit'], oid, f"rsi2={e['rsi2']:.1f}")
            if live:
                state.add_position(e['symbol'], e['qty'], e['limit'],
                                   str(datetime.date.today()), dh.security_id(e['symbol']), oid)
        except Exception as ex:
            print(f"  FAILED BUY {e['symbol']}: {ex}"); state.log('ERROR', e['symbol'], note=str(ex))

    if plan['hedge_required']:
        print("\n  REMINDER: hedge regime active — place/maintain the NIFTY futures short manually.")
    print("\n  Verify fills in ~10 min:  python -m live.trader --mode status")

def latest_plan():
    """Most recent plan persisted by build_plan. Stored in live.db (synced to R2),
    so the morning paper run can read the plan the evening scan produced even
    though it runs in a fresh CI job."""
    c = state.conn()
    row = c.execute("SELECT payload FROM runs ORDER BY ts DESC LIMIT 1").fetchone()
    c.close()
    return json.loads(row['payload']) if row else None

def paper_execute():
    """Apply the latest plan to the LOCAL/paper book only — no broker, no Dhan.
    Closes exit-flagged holdings and opens the day's entry signals, so the paper
    Positions/Portfolio tabs track the strategy. Entries fill at the plan limit,
    exits at the plan ref price. Entry date = today (the morning it is taken)."""
    plan = latest_plan()
    if not plan:
        print("no plan to execute — run --mode eod first"); return
    today = str(datetime.date.today())
    print(f"PAPER execute — plan for {plan.get('for_session')} "
          f"({len(plan['exits'])} exits, {len(plan['entries'])} entries)")

    # exits first (frees slots, realises P&L into the journal)
    for e in plan['exits']:
        px = float(e.get('ref_price') or 0)
        state.log('EXIT', e['symbol'], 'SELL', e.get('qty'), px, 'PAPER', e.get('reason'))
        state.close_position(e['symbol'])
        print(f"  EXIT  {e['symbol']:<16} qty {e.get('qty'):<6} @ {px:<10.2f} [{e.get('reason')}]")

    # entries
    held = {p['symbol'] for p in state.open_positions()}
    for e in plan['entries']:
        if e['symbol'] in held:
            print(f"  skip  {e['symbol']} (already held)"); continue
        px = float(e.get('limit') or e.get('ref_price') or 0)
        qty = int(e.get('qty') or 0)
        if qty < 1 or px <= 0:
            continue
        state.log('ENTRY', e['symbol'], 'BUY', qty, px, 'PAPER', f"rsi2={e.get('rsi2', 0):.1f}")
        state.add_position(e['symbol'], qty, px, today, '', 'PAPER')
        print(f"  ENTRY {e['symbol']:<16} qty {qty:<6} @ {px:<10.2f} rsi2 {e.get('rsi2', 0):.1f}")

    print(f"paper book now: {len(state.open_positions())} open")

def status(args):
    dh = Dhan(dry_run=not armed(args))
    print("LOCAL BOOK:")
    for p in state.open_positions():
        print(f"  {p['symbol']:<16} qty {p['qty']:<6} @ {p['entry_px']:<9} since {p['entry_date']}")
    if not armed(args):
        print("\n(dry run — not querying broker)"); return
    try:
        print("\nBROKER POSITIONS:")
        for p in dh.positions():
            print(f"  {p.get('tradingSymbol'):<16} net {p.get('netQty')}")
        print("\nBROKER ORDERS TODAY:")
        for o in dh.orders():
            print(f"  {o.get('tradingSymbol'):<16} {o.get('transactionType'):<5} "
                  f"{o.get('quantity'):<6} {o.get('orderStatus')}")
        f = dh.funds()
        print(f"\nAVAILABLE BALANCE: {f.get('availabelBalance', f.get('availableBalance','?'))}")
    except Exception as e:
        print(f"broker query failed: {e}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['check','eod','paper','open','status'], required=True)
    ap.add_argument('--live', action='store_true', help='send real orders (also needs MRV5_ARM=YES)')
    ap.add_argument('--source', choices=['db','dhan','mirror'], default='db')
    ap.add_argument('--paper', action='store_true', help='force paper mode (no orders)')
    ap.add_argument('--allow-stale', action='store_true', help='proceed on stale data')
    a = ap.parse_args()
    if a.mode == 'check':
        from mrv5 import env
        ok = env.check(need_dhan=True)
        if ok:
            print("\nTesting Dhan connection...")
            try:
                d = Dhan(dry_run=True)
                f = d.funds()
                bal = f.get('availabelBalance', f.get('availableBalance', '?'))
                print(f"  connected. available balance: {bal}")
                sid = d.security_id('sbin')
                print(f"  scrip lookup: sbin -> {sid}")
                print("\n  Credentials work. Next: python -m live.ingest --mode seed")
            except Exception as e:
                print(f"  FAILED: {e}")
                print("  Check the token has not expired (they last 30 days).")
        return
    if a.mode == 'eod':      show(build_plan(a.source, a.allow_stale))
    elif a.mode == 'paper':  paper_execute()
    elif a.mode == 'open':   execute(a)
    else:                    status(a)

if __name__ == '__main__':
    main()
