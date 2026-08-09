"""TradingView-style metrics."""
import numpy as np, pandas as pd

def metrics(E, T, capital):
    r = E.pct_change().dropna(); dn = r[r<0]
    m = E.resample('ME').last().pct_change().dropna()
    yrs = (E.index[-1]-E.index[0]).days/365.25
    dd = (E-E.cummax())/E.cummax()
    p = T.pnl; win = p[p>0]; los = p[p<=0]
    cw=cl=mw=ml=0
    for v in (p>0).values:
        cw = cw+1 if v else 0; cl = 0 if v else cl+1
        mw, ml = max(mw,cw), max(ml,cl)
    return dict(
        net=p.sum(), net_pct=p.sum()/capital*100, final=E.iloc[-1],
        gross_profit=win.sum(), gross_loss=los.sum(),
        commission=(T.notional*0.0025).sum(),
        cagr=((E.iloc[-1]/capital)**(1/yrs)-1)*100, monthly=m.mean()*100,
        sharpe=r.mean()/r.std()*np.sqrt(252) if r.std()>0 else 0,
        sortino=r.mean()/dn.std()*np.sqrt(252) if len(dn)>1 else np.nan,
        max_dd=dd.min()*100, calmar=((E.iloc[-1]/capital)**(1/yrs)-1)/-dd.min(),
        profit_factor=win.sum()/abs(los.sum()) if len(los) else np.inf,
        trades=len(T), per_year=len(T)/yrs, win_rate=(p>0).mean()*100,
        avg_ret=T.ret.mean(), avg_win=win.mean(), avg_loss=los.mean(),
        ratio=win.mean()/abs(los.mean()) if len(los) else np.inf,
        largest_win=p.max(), largest_loss=p.min(),
        avg_hold=T.hold.mean(), max_cons_win=mw, max_cons_loss=ml,
        pos_months=(m>0).mean()*100, best_month=m.max()*100, worst_month=m.min()*100,
        years=yrs)

def show(E, T, capital, label=''):
    k = metrics(E, T, capital)
    print(f"\n{'='*66}\n  {label}\n{'='*66}")
    rows = [
        ('Net profit', f"Rs {k['net']:,.0f}", f"{k['net_pct']:.1f}%"),
        ('Final equity', f"Rs {k['final']:,.0f}", ''),
        ('CAGR', f"{k['cagr']:.2f}%", f"monthly {k['monthly']:.2f}%"),
        ('Max drawdown', f"{k['max_dd']:.2f}%", f"Calmar {k['calmar']:.2f}"),
        ('Sharpe / Sortino', f"{k['sharpe']:.2f} / {k['sortino']:.2f}", ''),
        ('Profit factor', f"{k['profit_factor']:.3f}", ''),
        ('Commission paid', f"Rs {k['commission']:,.0f}", ''),
        ('Trades', f"{k['trades']}", f"{k['per_year']:.0f}/yr"),
        ('Win rate', f"{k['win_rate']:.2f}%", f"avg {k['avg_ret']:.2f}%"),
        ('Avg win / loss', f"Rs {k['avg_win']:,.0f} / Rs {k['avg_loss']:,.0f}",
         f"ratio {k['ratio']:.2f}"),
        ('Largest win / loss', f"Rs {k['largest_win']:,.0f} / Rs {k['largest_loss']:,.0f}", ''),
        ('Avg hold', f"{k['avg_hold']:.1f} d", ''),
        ('Max consec W / L', f"{k['max_cons_win']} / {k['max_cons_loss']}", ''),
        ('Positive months', f"{k['pos_months']:.0f}%",
         f"best {k['best_month']:.1f}% worst {k['worst_month']:.1f}%"),
    ]
    for a,b,c in rows: print(f"  {a:<22}{b:>30}{c:>14}")
    return k

def monthly_table(E):
    m = E.resample('ME').last().pct_change().dropna()*100
    t = pd.DataFrame({'y':m.index.year,'m':m.index.month,'v':m.values}).pivot(index='y',columns='m',values='v')
    t.columns = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][:t.shape[1]]
    t['YEAR'] = ((1+t.fillna(0)/100).prod(axis=1)-1)*100
    return t.round(1)
