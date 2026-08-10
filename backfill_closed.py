#!/usr/bin/env python3
"""Backfill the paper journal with the backtest's closed trades in a date
window, so the Closed Trades section shows recent history. Sizes qty for the
paper account (slot = equity/SLOTS, capped by MAX_ORDER). Idempotent: clears
prior BACKTEST_HIST rows first, and never touches open positions or the real
paper/seed journal entries.

    python backfill_closed.py --since 2026-07-01           # up to today
    python backfill_closed.py --since 2026-07-01 --until 2026-08-01

Heavy (runs the backtest) — run locally or in CI, not on the free web instance.
"""
import os, sys, argparse, datetime
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mrv5 import env as _env  # loads .env
from mrv5 import config as C
from live import state
from seed_paper import run_sim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-07-01', help='include trades whose EXIT is on/after this date')
    ap.add_argument('--until', default=None, help='default: today')
    a = ap.parse_args()
    since = pd.Timestamp(a.since)
    until = pd.Timestamp(a.until) if a.until else pd.Timestamp(datetime.date.today())

    E, T, open_book, asof = run_sim()
    win = T[(T.exit_dt >= since) & (T.exit_dt <= until)].sort_values('exit_dt')
    print(f"backtest as-of {asof}: {len(win)} closed trades with exit in "
          f"[{since.date()}, {until.date()}]")

    equity = float(os.environ.get('MRV5_EQUITY', C.CAPITAL))
    max_order = float(os.environ.get('MRV5_MAX_ORDER', 200_000))
    slot = min(equity / C.SLOTS, max_order)

    c = state.conn()
    c.execute("DELETE FROM journal WHERE order_id='BACKTEST_HIST'")
    c.commit(); c.close()

    n = 0
    for r in win.itertuples():
        ep, xp = round(float(r.entry_px), 2), round(float(r.exit_px), 2)
        qty = int(slot / ep)
        if qty < 1:
            continue
        ed = str(pd.Timestamp(r.entry_dt).date())
        xd = str(pd.Timestamp(r.exit_dt).date())
        c = state.conn()
        c.execute("INSERT INTO journal VALUES(?,?,?,?,?,?,?,?)",
                  (ed + 'T09:15:00', 'ENTRY', r.symbol, 'BUY', qty, ep, 'BACKTEST_HIST', f'hist {r.typ}'))
        c.execute("INSERT INTO journal VALUES(?,?,?,?,?,?,?,?)",
                  (xd + 'T15:30:00', 'EXIT', r.symbol, 'SELL', qty, xp, 'BACKTEST_HIST', str(r.typ)))
        c.commit(); c.close()
        n += 1
        print(f"  {r.symbol:<16} {ed} -> {xd}  qty {qty:<5} {ep:>9.2f} -> {xp:<9.2f} [{r.typ}]")

    from live import portfolio
    print(f"\nbackfilled {n} closed trades; realised_trades now sees "
          f"{len(portfolio.closed_trades_list())} closed")


if __name__ == '__main__':
    main()
