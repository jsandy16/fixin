"""Indicators, matrix prep, and the event-driven portfolio simulator.

Timing convention (no lookahead anywhere):
  signal evaluated on bar t-1 close -> order filled at bar t OPEN
  exits evaluated on bar t and filled at bar t CLOSE (or at the stop level)
  all regime series are lagged one day before use
"""
import numpy as np, pandas as pd
from . import config as C

def rsi(s, n):
    d = s.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1/n, adjust=False).mean() / dn.ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1+rs)

def prep(d):
    """Add all indicator columns for one symbol."""
    if d is None or len(d) < C.MIN_HISTORY: return None
    d = d.copy()
    d['rsi2']   = rsi(d.Close, C.RSI_LEN)
    d['smaT']   = d.Close.rolling(C.TREND_SMA).mean()
    d['smaX']   = d.Close.rolling(C.EXIT_SMA).mean()
    d['signal'] = (d.Close > d.smaT) & (d.rsi2 < C.BUY_BELOW)
    return d

def to_matrices(data, start=None, end=None):
    """dict{sym: df} -> aligned numpy matrices. Much faster than per-day pandas."""
    cal = sorted(set().union(*[set(d.index) for d in data.values()]))
    if start: cal = [t for t in cal if t >= pd.Timestamp(start)]
    if end:   cal = [t for t in cal if t <= pd.Timestamp(end)]
    syms = sorted(data)
    cols = ['Open','High','Low','Close','rsi2','smaX']
    M = {c: np.full((len(cal), len(syms)), np.nan) for c in cols}
    M['signal'] = np.zeros((len(cal), len(syms)), bool)
    pos = {t: i for i, t in enumerate(cal)}
    for j, s in enumerate(syms):
        d = data[s]
        d = d[(d.index >= cal[0]) & (d.index <= cal[-1])]
        ii = [pos[t] for t in d.index if t in pos]
        d = d[d.index.isin(pos)]
        for c in cols: M[c][ii, j] = d[c].values
        M['signal'][ii, j] = d['signal'].values
    return cal, syms, M

def breadth(M, cal, win=200):
    """Fraction of universe above its own SMA(win). Lagged 1 day by caller."""
    Cl = pd.DataFrame(M['Close'], index=pd.DatetimeIndex(cal))
    above = Cl > Cl.rolling(win).mean()
    valid = Cl.notna()
    return ((above & valid).sum(axis=1) / valid.sum(axis=1).replace(0, np.nan)).fillna(0.5)

def simulate(cal, syms, M, hedge_mask=None, idx_ret=None, capital=None, slots=None,
             cost=None, compound=None, max_hold=None, exit_above=None,
             use_stop=None, stop_pct=None, eq_brake=None, hedge_ratio=None,
             entry_gate=None):
    """Returns (equity Series, trades DataFrame)."""
    capital  = capital  if capital  is not None else C.CAPITAL
    slots    = slots    if slots    is not None else C.SLOTS
    cost     = cost     if cost     is not None else C.COST_ROUNDTRIP
    compound = compound if compound is not None else C.COMPOUND
    max_hold = max_hold if max_hold is not None else C.MAX_HOLD_DAYS
    exit_above = exit_above if exit_above is not None else C.EXIT_ABOVE
    use_stop = use_stop if use_stop is not None else C.USE_STOP_LOSS
    stop_pct = stop_pct if stop_pct is not None else C.STOP_PCT
    hedge_ratio = hedge_ratio if hedge_ratio is not None else C.HEDGE_RATIO
    if eq_brake is None and C.EQ_BRAKE_ON: eq_brake = (C.EQ_BRAKE_PCT, C.EQ_BRAKE_WIN)

    O,H,L,Cl = M['Open'],M['High'],M['Low'],M['Close']
    R2, SX, SIG = M['rsi2'], M['smaX'], M['signal']
    n_t, n_s = len(cal), len(syms)
    pos = {}; cash = capital; eq = np.empty(n_t); trades = []
    slot_cap = capital / slots; hedged_prev = False

    for i in range(n_t):
        # ---- hedge P&L on yesterday's long book ----
        if hedge_mask is not None and i > 0 and hedge_mask[i-1]:
            pmv = sum((q*Cl[i-1,j] if np.isfinite(Cl[i-1,j]) else nt)
                      for j,(q,ep,ei,nt) in pos.items())
            cash += -idx_ret[i] * pmv * hedge_ratio
            if not hedged_prev: cash -= pmv * C.HEDGE_TOGGLE_COST
            hedged_prev = True
        elif hedge_mask is not None:
            hedged_prev = False

        # ---- exits ----
        for j in list(pos):
            if not np.isfinite(Cl[i,j]): continue
            q, ep, ei, nt = pos[j]
            age = (cal[i]-cal[ei]).days
            xp = typ = None
            if use_stop and L[i,j] <= ep*(1-stop_pct):
                xp, typ = ep*(1-stop_pct), 'Stop'
            elif (R2[i,j] > exit_above) or (np.isfinite(SX[i,j]) and Cl[i,j] > SX[i,j]):
                xp, typ = Cl[i,j], 'Signal'
            elif age >= max_hold:
                xp, typ = Cl[i,j], 'Time'
            if xp is not None:
                pnl = q*(xp-ep) - nt*cost
                cash += nt + pnl
                trades.append((syms[j], cal[ei], cal[i], ep, xp, q, nt,
                               pnl, (xp-ep)/ep*100 - cost*100, age, typ))
                del pos[j]

        # ---- position sizing base ----
        if compound and i > 0:
            cur = cash + sum((q*Cl[i-1,j] if np.isfinite(Cl[i-1,j]) else nt)
                             for j,(q,ep,ei,nt) in pos.items())
            growth = cur - capital
            if compound == 'half': cur = capital + 0.5*growth
            slot_cap = max(cur, capital*0.2) / slots

        # ---- entries ----
        free = slots - len(pos)
        if eq_brake is not None and i > 1:
            w = eq[max(0, i-eq_brake[1]):i]
            if len(w) and eq[i-1]/w.max() - 1 < -eq_brake[0]: free = 0
        if entry_gate is not None and i > 0 and not entry_gate[i-1]: free = 0
        if free > 0 and i > 0:
            cands = [(R2[i-1,j], j) for j in range(n_s)
                     if j not in pos and SIG[i-1,j] and np.isfinite(O[i,j]) and O[i,j] > 0]
            cands.sort()
            for _, j in cands[:free]:
                q = int(slot_cap / O[i,j])
                if q < 1: continue
                pos[j] = (q, O[i,j], i, q*O[i,j]); cash -= q*O[i,j]

        eq[i] = cash + sum((q*Cl[i,j] if np.isfinite(Cl[i,j]) else nt)
                           for j,(q,ep,ei,nt) in pos.items())

    E = pd.Series(eq, index=pd.DatetimeIndex(cal))
    T = pd.DataFrame(trades, columns=['symbol','entry_dt','exit_dt','entry_px','exit_px',
                                      'qty','notional','pnl','ret','hold','typ'])
    return E, T
