#!/usr/bin/env python3
"""Run the full portfolio backtest. Edit mrv5/config.py, then: python backtest.py"""
import sys, pickle, argparse, numpy as np, pandas as pd
from mrv5 import config as C, data, engine, report

def build(refresh=False):
    print("building universe...")
    syms = data.build_universe(C.UNIVERSE_SIZE, C.TURNOVER_LOOKBACK, refresh=refresh)
    print(f"  {len(syms)} symbols")
    prepped = {}
    for s in syms:
        d = engine.prep(data.load(s))
        if d is not None: prepped[s] = d
    print(f"  {len(prepped)} with sufficient history")
    cal, sy, M = engine.to_matrices(prepped, C.START, C.END)
    print(f"  {len(cal)} trading days, {len(sy)} symbols")
    idx = data.load(C.HEDGE_INDEX)
    ci = pd.DatetimeIndex(cal)
    ixs = idx.Close.reindex(ci).ffill().bfill()
    idx_ret = ixs.pct_change().fillna(0).values
    below = (ixs < ixs.rolling(C.HEDGE_INDEX_SMA).mean()).values
    br = engine.breadth(M, cal).shift(1).fillna(0.5).values
    hedge = (below | (br < C.HEDGE_BREADTH_MIN)) if C.HEDGE_ON else None
    return dict(cal=cal, syms=sy, M=M, hedge=hedge, idx_ret=idx_ret, breadth=br)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true', help='re-download all data')
    ap.add_argument('--rebuild', action='store_true', help='rebuild matrices')
    ap.add_argument('--sweep', default=None,
                    help="param sweep, e.g. --sweep 'COST_ROUNDTRIP=0.0025,0.004,0.006'")
    ap.add_argument('--symbol', default=None, help='print all trades for one symbol')
    a = ap.parse_args()

    try:
        if a.rebuild or a.refresh: raise FileNotFoundError
        B = pickle.load(open('cache/_matrices.pkl','rb'))
        print("loaded cached matrices (use --rebuild to refresh)")
    except Exception:
        B = build(a.refresh); pickle.dump(B, open('cache/_matrices.pkl','wb'))

    kw = dict(hedge_mask=B['hedge'], idx_ret=B['idx_ret'])

    if a.sweep:
        name, vals = a.sweep.split('=')
        print(f"\nSWEEP {name}")
        INT_PARAMS = {'slots','max_hold','eq_brake_win'}
        for v in vals.split(','):
            key = name.lower()
            v = int(float(v)) if key in INT_PARAMS else float(v)
            E, T = engine.simulate(B['cal'], B['syms'], B['M'],
                                   **{**kw, key: v})
            k = report.metrics(E, T, C.CAPITAL)
            print(f"  {name}={str(v):<10} monthly={k['monthly']:5.2f}% Sharpe={k['sharpe']:5.2f} "
                  f"Sortino={k['sortino']:5.2f} DD={k['max_dd']:6.1f}% trades/yr={k['per_year']:.0f}")
        return

    E, T = engine.simulate(B['cal'], B['syms'], B['M'], **kw)
    report.show(E, T, C.CAPITAL, 'MR-v5 PORTFOLIO BACKTEST')
    print("\nMonthly returns %:"); print(report.monthly_table(E).to_string())

    # walk-forward
    mid = E.index[len(E)//2]
    for lo, hi, lab in [(E.index[0], mid, 'IN-SAMPLE'), (mid, E.index[-1], 'OUT-OF-SAMPLE')]:
        Ei = E[(E.index>=lo)&(E.index<=hi)]; Ti = T[(T.entry_dt>=lo)&(T.entry_dt<=hi)]
        if len(Ti) > 20:
            k = report.metrics(Ei, Ti, Ei.iloc[0])
            print(f"\n  {lab} {lo.date()}→{hi.date()}: monthly={k['monthly']:.2f}% "
                  f"Sharpe={k['sharpe']:.2f} Sortino={k['sortino']:.2f} DD={k['max_dd']:.1f}%")

    import os, datetime
    os.makedirs('out', exist_ok=True)
    E.to_csv('out/equity.csv'); T.to_csv('out/trades.csv', index=False)

    # ---- dashboard generated FROM this run's output ----
    from mrv5 import dashboard
    bench = None
    try:
        bi = data.load(C.HEDGE_INDEX)
        bench = bi.Close.reindex(E.index).ffill().bfill()
    except Exception:
        pass
    meta = dict(title='MR-v5 backtest', generated=str(datetime.datetime.now())[:16],
                universe=len(B['syms']), start=str(E.index[0].date()), end=str(E.index[-1].date()),
                capital=C.CAPITAL, slots=C.SLOTS, cost=C.COST_ROUNDTRIP,
                compound=str(C.COMPOUND), max_hold=C.MAX_HOLD_DAYS,
                buy_below=C.BUY_BELOW, exit_above=C.EXIT_ABOVE, trend_sma=C.TREND_SMA,
                stop_loss=str(C.USE_STOP_LOSS), hedge=str(C.HEDGE_ON),
                eq_brake=f"{C.EQ_BRAKE_PCT:.0%}/{C.EQ_BRAKE_WIN}d" if C.EQ_BRAKE_ON else 'off')
    dashboard.render(dashboard.build_payload(E, T, C.CAPITAL, bench, meta), 'out/dashboard.html')
    from mrv5 import results
    results.save(results.build(E, T, C.CAPITAL, bench, meta))
    print("\nwrote out/equity.csv, out/trades.csv, out/dashboard.html, out/results.json")

    if a.symbol:
        s = T[T.symbol == a.symbol].copy()
        if len(s):
            s['cum'] = s.pnl.cumsum()
            print(f"\nALL TRADES — {a.symbol} ({len(s)} trades, net Rs {s.pnl.sum():,.0f})")
            print(s.to_string(index=False))
        else:
            print(f"\nno trades for {a.symbol}")

if __name__ == '__main__':
    main()
