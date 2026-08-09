#!/usr/bin/env python3
"""Daily signal generator. Run after market close; prints what to trade tomorrow.
This is the bridge between the backtest and any broker API."""
import json, sys, numpy as np, pandas as pd
from mrv5 import config as C, data, engine

def today_signals(hold_syms=None):
    hold_syms = set(hold_syms or [])
    syms = data.build_universe(C.UNIVERSE_SIZE, C.TURNOVER_LOOKBACK)
    entries, exits = [], []
    closes = {}
    for s in syms:
        d = engine.prep(data.load(s, refresh=True))
        if d is None or len(d) < 2: continue
        last = d.iloc[-1]
        closes[s] = last.Close
        if s in hold_syms:
            if (last.rsi2 > C.EXIT_ABOVE) or (last.Close > last.smaX):
                exits.append(dict(symbol=s, reason='signal', close=round(last.Close,2)))
        elif bool(last.signal):
            entries.append(dict(symbol=s, rsi2=round(last.rsi2,2), close=round(last.Close,2)))
    entries.sort(key=lambda x: x['rsi2'])          # deepest first

    # regime check
    idx = data.load(C.HEDGE_INDEX, refresh=True)
    below = idx.Close.iloc[-1] < idx.Close.rolling(C.HEDGE_INDEX_SMA).mean().iloc[-1]
    above_cnt = sum(1 for s in syms
                    if (d := data.load(s)) is not None and len(d) > 200
                    and d.Close.iloc[-1] > d.Close.rolling(200).mean().iloc[-1])
    br = above_cnt / max(len(syms), 1)
    return dict(date=str(pd.Timestamp.today().date()),
                entries=entries, exits=exits,
                free_slots=C.SLOTS - len(hold_syms),
                hedge_required=bool(below or br < C.HEDGE_BREADTH_MIN),
                breadth=round(br, 3), index_below_sma=bool(below))

if __name__ == '__main__':
    holds = sys.argv[1:] if len(sys.argv) > 1 else []
    out = today_signals(holds)
    print(json.dumps(out, indent=2)[:4000])
    n = out['free_slots']
    print(f"\n>>> BUY tomorrow at open (top {n} by lowest RSI2):")
    for e in out['entries'][:max(n,0)]:
        print(f"    {e['symbol']:<16} RSI2={e['rsi2']:<6} close={e['close']}")
    print(f">>> SELL at close: {[e['symbol'] for e in out['exits']] or 'none'}")
    print(f">>> HEDGE: {'ON — short NIFTY ~1x long notional' if out['hedge_required'] else 'OFF'}")
