"""Precomputed backtest + forward-test results, cached to disk so the dashboard
serves instantly instead of waiting on a simulation."""
import os, json, datetime
import pandas as pd, numpy as np
from . import config as C, analytics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH     = os.environ.get('MRV5_RESULTS') or os.path.join(ROOT, 'out', 'results.json')
FALLBACK = os.path.join(ROOT, 'out', 'results.json')   # copy shipped in the repo

def split_date():
    """Backtest = in-sample. Forward test = held-out tail, untouched by tuning."""
    return os.environ.get('MRV5_SPLIT', C.__dict__.get('SPLIT_DATE', '2020-01-01'))

def build(E, T, capital, bench=None, meta=None):
    sp = pd.Timestamp(split_date())
    out = dict(generated=str(datetime.datetime.now())[:19], split=str(sp.date()),
               meta=meta or {}, capital=capital)
    for name, (lo, hi) in dict(
            full=(E.index[0], E.index[-1]),
            backtest=(E.index[0], sp),
            forward=(sp, E.index[-1])).items():
        Ei = E[(E.index >= lo) & (E.index <= hi)]
        Ti = T[(pd.to_datetime(T.entry_dt) >= lo) & (pd.to_datetime(T.entry_dt) <= hi)]
        if len(Ei) < 5 or len(Ti) == 0:
            out[name] = dict(empty=True, label=name); continue
        bi = bench[(bench.index >= lo) & (bench.index <= hi)] if bench is not None else None
        base = float(Ei.iloc[0])
        out[name] = analytics.full(Ei, Ti, base, bi, label=name)
        out[name]['series'] = analytics.series(Ei, bi)
        out[name]['monthly'] = analytics.monthly(Ei)
        out[name]['breakdowns'] = analytics.breakdowns(Ti)
    return out

def save(payload, path=None):
    p = path or PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(payload, open(p, 'w'), default=str)
    return p

def load(path=None):
    """Prefer the persistent-disk copy; fall back to the one shipped in the repo.
    This is what makes a fresh Render deploy show data before any backtest runs."""
    for p in [path or PATH, FALLBACK]:
        if p and os.path.exists(p):
            try: return json.load(open(p))
            except Exception: continue
    return None
