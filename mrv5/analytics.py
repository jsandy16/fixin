"""Full analytics for a trade set + equity curve — the TradingView metric set,
plus the risk stats TradingView omits. Used by both backtest and forward test."""
import numpy as np, pandas as pd

def _safe(v):
    if v is None: return None
    try:
        f = float(v)
        return None if not np.isfinite(f) else round(f, 6)
    except Exception: return v

def full(E, T, capital, bench=None, label=''):
    """E: equity Series (daily, MTM). T: trades DataFrame. Returns a dict."""
    if E is None or len(E) < 5 or T is None or len(T) == 0:
        return dict(label=label, empty=True)
    E = E.sort_index()
    r  = E.pct_change().dropna()
    m  = E.resample('ME').last().pct_change().dropna()
    yrs = max((E.index[-1] - E.index[0]).days / 365.25, 1e-9)
    dd_s = (E - E.cummax()) / E.cummax()
    p = T.pnl; win = p[p > 0]; los = p[p <= 0]
    dn = r[r < 0]; mdn = m[m < 0]

    # streaks
    cw = cl = mw = ml = 0
    for v in (p > 0).values:
        cw = cw + 1 if v else 0; cl = 0 if v else cl + 1
        mw, ml = max(mw, cw), max(ml, cl)

    # drawdown episodes
    eps, start, peak = [], None, None
    for dt, v in dd_s.items():
        if v < -0.01 and start is None: start, peak = dt, v
        elif start is not None:
            peak = min(peak, v)
            if v >= -0.001:
                eps.append(dict(start=str(start.date()), end=str(dt.date()),
                                depth=round(peak*100, 2), days=(dt-start).days))
                start = None
    if start is not None:
        eps.append(dict(start=str(start.date()), end=str(E.index[-1].date()),
                        depth=round(peak*100, 2), days=(E.index[-1]-start).days))
    eps = sorted(eps, key=lambda x: x['depth'])[:8]

    # recovery from max DD
    tro = dd_s.idxmin()
    rec = E[E.index > tro]
    rec_days = None
    if len(rec):
        back = rec[rec >= E[:tro].max()]
        rec_days = (back.index[0] - tro).days if len(back) else None

    k = dict(
        label=label, empty=False,
        start=str(E.index[0].date()), end=str(E.index[-1].date()), years=round(yrs, 2),
        # returns
        net=_safe(p.sum()), net_pct=_safe(p.sum()/capital*100),
        final=_safe(E.iloc[-1]), initial=_safe(capital),
        gross_profit=_safe(win.sum()), gross_loss=_safe(los.sum()),
        commission=_safe((T.notional*0.0025).sum()) if 'notional' in T else None,
        cagr=_safe(((E.iloc[-1]/capital)**(1/yrs)-1)*100),
        monthly=_safe(m.mean()*100), monthly_med=_safe(m.median()*100),
        # risk
        sharpe=_safe(r.mean()/r.std()*np.sqrt(252) if r.std() > 0 else 0),
        sortino=_safe(r.mean()/dn.std()*np.sqrt(252) if len(dn) > 1 and dn.std() > 0 else None),
        sharpe_monthly=_safe(m.mean()/m.std()*np.sqrt(12) if m.std() > 0 else 0),
        sortino_monthly=_safe(m.mean()/mdn.std()*np.sqrt(12) if len(mdn) > 1 and mdn.std() > 0 else None),
        max_dd=_safe(dd_s.min()*100), max_dd_date=str(tro.date()),
        max_dd_recovery_days=rec_days,
        max_runup=_safe(((E-E.cummin())/E.cummin()).max()*100),
        calmar=_safe(((E.iloc[-1]/capital)**(1/yrs)-1)/-dd_s.min() if dd_s.min() < 0 else None),
        ann_vol=_safe(r.std()*np.sqrt(252)*100), monthly_vol=_safe(m.std()*100),
        skew=_safe(r.skew()), kurtosis=_safe(r.kurtosis()),
        var95=_safe(np.percentile(r, 5)*100), cvar95=_safe(r[r <= np.percentile(r, 5)].mean()*100),
        ulcer=_safe(np.sqrt((dd_s**2).mean())*100),
        # trades
        trades=int(len(T)), per_year=_safe(len(T)/yrs),
        wins=int(len(win)), losses=int(len(los)),
        win_rate=_safe((p > 0).mean()*100),
        profit_factor=_safe(win.sum()/abs(los.sum()) if len(los) and los.sum() != 0 else None),
        expectancy=_safe(p.mean()), expectancy_pct=_safe(T.ret.mean()),
        avg_win=_safe(win.mean()), avg_loss=_safe(los.mean()),
        avg_win_pct=_safe(T.ret[T.ret > 0].mean()), avg_loss_pct=_safe(T.ret[T.ret <= 0].mean()),
        payoff=_safe(win.mean()/abs(los.mean()) if len(los) and los.mean() != 0 else None),
        largest_win=_safe(p.max()), largest_loss=_safe(p.min()),
        largest_win_pct=_safe(T.ret.max()), largest_loss_pct=_safe(T.ret.min()),
        avg_hold=_safe(T.hold.mean()),
        avg_hold_win=_safe(T.hold[p > 0].mean()), avg_hold_loss=_safe(T.hold[p <= 0].mean()),
        max_cons_win=mw, max_cons_loss=ml,
        # months
        pos_months=_safe((m > 0).mean()*100), best_month=_safe(m.max()*100),
        worst_month=_safe(m.min()*100), months=int(len(m)),
        breakeven_win_rate=_safe(100/(1+win.mean()/abs(los.mean())) if len(los) and los.mean() != 0 else None),
        dd_episodes=eps,
    )
    if bench is not None and len(bench) > 2:
        b = bench.reindex(E.index).ffill().bfill()
        br = b.pct_change().dropna()
        bdd = ((b-b.cummax())/b.cummax()).min()
        common = r.index.intersection(br.index)
        beta = float(np.polyfit(br[common], r[common], 1)[0]) if len(common) > 20 else None
        k['bench'] = dict(
            total=_safe((b.iloc[-1]/b.iloc[0]-1)*100),
            cagr=_safe(((b.iloc[-1]/b.iloc[0])**(1/yrs)-1)*100),
            sharpe=_safe(br.mean()/br.std()*np.sqrt(252) if br.std() > 0 else 0),
            max_dd=_safe(bdd*100), beta=_safe(beta),
            alpha=_safe((((E.iloc[-1]/capital)**(1/yrs)-1) -
                         (beta or 0)*((b.iloc[-1]/b.iloc[0])**(1/yrs)-1))*100) if beta is not None else None,
            corr=_safe(r[common].corr(br[common])) if len(common) > 20 else None)
    return k

def series(E, bench=None, n=430):
    step = max(1, len(E)//n)
    out = dict(x=[str(x.date()) for x in E.index[::step]],
               y=[round(float(v)) for v in E.values[::step]],
               dd=[round(float(v)*100, 2) for v in ((E-E.cummax())/E.cummax()).values[::step]])
    if bench is not None:
        b = bench.reindex(E.index).ffill().bfill()
        out['bench'] = [round(float(E.iloc[0])*float(v)/float(b.iloc[0])) for v in b.values[::step]]
    return out

def monthly(E):
    m = E.resample('ME').last().pct_change().dropna()*100
    if not len(m): return dict(years=[], data=[])
    t = pd.DataFrame({'y': m.index.year, 'm': m.index.month, 'v': m.values}).pivot(index='y', columns='m', values='v')
    t = t.reindex(columns=range(1, 13))
    return dict(years=[int(i) for i in t.index],
                data=[[None if pd.isna(v) else round(float(v), 2) for v in row] for row in t.values])

def breakdowns(T):
    out = {}
    g = T.groupby('typ').agg(n=('pnl','size'), tot=('pnl','sum'),
        win=('ret', lambda s: (s > 0).mean()*100), avg=('ret','mean'), hold=('hold','mean'))
    out['exits'] = [[i]+[round(float(v), 2) for v in r] for i, r in g.iterrows()]
    s = T.groupby('symbol').agg(n=('pnl','size'), tot=('pnl','sum'),
        win=('ret', lambda x: (x > 0).mean()*100), avg=('ret','mean')).sort_values('tot', ascending=False)
    out['symbols'] = [[i, int(r.n), round(float(r.tot)), round(float(r.win), 1),
                       round(float(r.avg), 2)] for i, r in s.iterrows()]
    T2 = T.copy(); T2['dur'] = pd.cut(T2.hold, [-1,1,3,5,10,20,999],
        labels=['1d','2-3d','4-5d','6-10d','11-20d','20d+'])
    h = T2.groupby('dur', observed=True).agg(n=('pnl','size'), tot=('pnl','sum'),
        win=('ret', lambda s: (s > 0).mean()*100), avg=('ret','mean'))
    out['holds'] = [[str(i)]+[round(float(v), 2) for v in r] for i, r in h.iterrows()]
    y = T.copy(); y['yr'] = pd.to_datetime(y.exit_dt).dt.year
    a = y.groupby('yr').agg(n=('pnl','size'), tot=('pnl','sum'),
        win=('ret', lambda s: (s > 0).mean()*100), avg=('ret','mean'))
    out['years'] = [[int(i)]+[round(float(v), 2) for v in r] for i, r in a.iterrows()]
    return out
