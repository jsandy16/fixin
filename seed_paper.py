#!/usr/bin/env python3
"""Seed the live/paper book from the backtest's currently-open positions.

Runs the same simulation the dashboard's Analytics backtest runs, takes the
positions still open on the last bar, and writes them into live.db as OPEN
paper positions with their real backtest entry date / price / qty. Run once to
start paper trading from the strategy's current holdings.

    python seed_paper.py          # wipe the OPEN book, then seed
    python seed_paper.py --keep   # keep existing positions, add only new ones

Heavy (loads the universe into pandas) — run locally or in CI, NOT on the
512Mi free web instance.
"""
import os, sys, argparse, datetime
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mrv5 import env as _env  # loads .env
from mrv5 import config as C, data, engine
from live import state


def build_open_book():
    """Reconstruct the positions the backtest is holding on the last bar."""
    syms = data.build_universe(C.UNIVERSE_SIZE, C.TURNOVER_LOOKBACK)
    prepped = {}
    for s in syms:
        d = engine.prep(data.load(s))
        if d is not None:
            prepped[s] = d
    cal, sy, M = engine.to_matrices(prepped, C.START, C.END)
    idx = data.load(C.HEDGE_INDEX)
    ci = pd.DatetimeIndex(cal)
    ixs = idx.Close.reindex(ci).ffill().bfill()
    below = (ixs < ixs.rolling(C.HEDGE_INDEX_SMA).mean()).values
    br = engine.breadth(M, cal).shift(1).fillna(0.5).values
    hedge = (below | (br < C.HEDGE_BREADTH_MIN)) if C.HEDGE_ON else None
    E, T, open_book = engine.simulate(
        cal, sy, M, hedge_mask=hedge,
        idx_ret=ixs.pct_change().fillna(0).values, return_open=True)
    return open_book, str(cal[-1].date())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', action='store_true', help='keep existing OPEN positions')
    a = ap.parse_args()

    book, asof = build_open_book()
    book.sort(key=lambda x: x['entry_date'])
    print(f"backtest as-of {asof}: {len(book)} open positions")

    if not a.keep:
        c = state.conn()
        c.execute("DELETE FROM positions")
        c.execute("DELETE FROM journal WHERE order_id='BACKTEST_SEED'")
        c.commit(); c.close()
        print("cleared existing positions + prior seed journal entries")

    # Size for the PAPER account, not the backtest's compounded equity. Mirrors
    # live.trader.build_plan: slot = equity / SLOTS, capped by MAX_ORDER.
    equity = float(os.environ.get('MRV5_EQUITY', C.CAPITAL))
    max_order = float(os.environ.get('MRV5_MAX_ORDER', 200_000))
    slot = min(equity / C.SLOTS, max_order)
    print(f"paper sizing: equity {equity:,.0f}  slot {slot:,.0f}  (backtest qtys ignored)")

    held = {p['symbol'] for p in state.open_positions()}
    added = 0
    for p in book:
        sym, px, ed = p['symbol'], round(float(p['entry_px']), 2), p['entry_date']
        qty = int(slot / px)
        if qty < 1:
            print(f"  skip {sym} (slot too small for px {px})")
            continue
        if sym in held:
            print(f"  skip {sym} (already held)")
            continue
        state.add_position(sym, qty, px, ed, '', 'BACKTEST_SEED')
        # journal ENTRY timestamped at the real entry date so hold-days stay
        # correct when this position is eventually closed by the trader.
        c = state.conn()
        c.execute("INSERT INTO journal VALUES(?,?,?,?,?,?,?,?)",
                  (ed + 'T09:15:00', 'ENTRY', sym, 'BUY', qty, px,
                   'BACKTEST_SEED', 'seeded from backtest'))
        c.commit(); c.close()
        added += 1
        print(f"  + {sym:<16} qty {qty:<6} @ {px:<10.2f} since {ed}")

    n = len(state.open_positions())
    print(f"\nseeded: +{added} added, book now {n} open positions")


if __name__ == '__main__':
    main()
