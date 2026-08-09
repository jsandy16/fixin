#!/usr/bin/env python3
"""First-boot helper for a fresh deploy: seed the price DB and produce results.json
so the dashboard has something to show immediately. Safe to re-run — it skips
steps that are already done.

    python bootstrap.py            # seed + backtest as needed
    python bootstrap.py --force    # redo both regardless
"""
import os, sys, argparse, subprocess

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, root)
    from live import db
    from mrv5 import results

    cov = db.coverage()
    have = cov.get('syms') or 0
    if a.force or have < 20:
        print(f"[1/2] seeding price DB (currently {have} symbols)...")
        subprocess.run([sys.executable, '-m', 'live.ingest', '--mode', 'seed'],
                       check=True, cwd=root)
    else:
        print(f"[1/2] DB has {have} symbols through {cov['mx']} — skipping seed")

    if a.force or results.load() is None:
        print("[2/2] running backtest to generate results.json...")
        subprocess.run([sys.executable, 'backtest.py'], check=True, cwd=root)
    else:
        print("[2/2] results.json present — skipping backtest")

    r = results.load()
    if r:
        f = r.get('full', {})
        print(f"\nready — full period: CAGR {f.get('cagr')}%  "
              f"Sharpe {f.get('sharpe')}  {f.get('trades')} trades")
    print("dashboard will serve immediately at /")

if __name__ == '__main__':
    main()
