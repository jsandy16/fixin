"""End-to-end test of the live path with a mocked Dhan endpoint.
Replays the documented DhanHQ v2 /charts/historical response shape so parsing,
DB upsert, freshness guard, signals and order construction are all exercised
exactly as they will be live. Only the network call is faked."""
import os, sys, datetime
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MRV5_DB'] = '/tmp/test_prices.db'
if os.path.exists('/tmp/test_prices.db'): os.remove('/tmp/test_prices.db')
from live import db
from live.dhan import Dhan
from mrv5 import config as C, engine

FAIL=[]
def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ''))
    if not cond: FAIL.append(name)

print("\n1. Dhan /charts/historical response parsing")
def fake_hist(days=300, seed=7, start_px=800.0):
    rng=np.random.default_rng(seed)
    dates=pd.bdate_range(end=pd.Timestamp('2026-08-07'), periods=days)
    px=start_px*np.exp(np.cumsum(rng.normal(0.0004,0.015,days)))
    o=px*(1+rng.normal(0,0.003,days))
    h=np.maximum(o,px)*(1+abs(rng.normal(0,0.005,days)))
    l=np.minimum(o,px)*(1-abs(rng.normal(0,0.005,days)))
    return {'open':list(o),'high':list(h),'low':list(l),'close':list(px),
            'volume':list(rng.integers(1e5,5e6,days).astype(float)),
            'timestamp':[int(pd.Timestamp(d).timestamp()) for d in dates]}

class MockDhan(Dhan):
    def __init__(self,**kw):
        super().__init__(dry_run=kw.pop('dry_run',True)); self.calls=[]
    def security_id(self,symbol):
        return {'sbin':'3045','infy':'1594','tcs':'11536','reliance':'2885','nifty 500':'999'}.get(symbol.lower(),'0001')
    def _req(self,method,path,**kw):
        self.calls.append((method,path,kw.get('json')))
        if path=='/charts/historical': return fake_hist()
        if path=='/fundlimit': return {'availabelBalance':1250000.0}
        if path=='/positions': return [{'tradingSymbol':'SBIN','netQty':114}]
        if path=='/orders' and method=='GET': return [{'tradingSymbol':'SBIN','transactionType':'BUY','quantity':114,'orderStatus':'TRADED'}]
        if path=='/orders': return {'orderId':'MOCK123','orderStatus':'PENDING'}
        return {}

dh=MockDhan()
d=dh.daily('sbin','2025-01-01','2026-08-07')
check("returns DataFrame", isinstance(d,pd.DataFrame))
check("OHLCV columns", list(d.columns)==['Open','High','Low','Close','Volume'], str(list(d.columns)))
check("datetime index", isinstance(d.index,pd.DatetimeIndex))
check("high >= low", bool((d.High>=d.Low).all()))
check("last bar 2026-08-07", str(d.index[-1].date())=='2026-08-07', str(d.index[-1].date()))
check("request body correct", dh.calls[0][2]['securityId']=='3045' and dh.calls[0][2]['exchangeSegment']=='NSE_EQ')

print("\n2. Database ingest + read-back")
n=db.upsert('sbin',d,'dhan',1)
check("rows written", n==len(d), str(n))
r=db.load('sbin')
check("read back matches", len(r)==len(d))
check("close preserved", abs(float(r.Close.iloc[-1])-float(d.Close.iloc[-1]))<1e-6)
db.upsert('sbin',d,'dhan',1)
check("idempotent re-ingest", len(db.load('sbin'))==len(d), str(len(db.load('sbin'))))
check("coverage", db.coverage()['syms']==1)

print("\n3. Provisional bar semantics (the 15:20 case)")
prov=pd.DataFrame({'Open':[1000.],'High':[1010.],'Low':[990.],'Close':[1005.],'Volume':[1e6]},index=pd.to_datetime(['2026-08-07']))
db.upsert('sbin',prov,'quote',0)
check("provisional does NOT overwrite settled", abs(float(db.load('sbin').Close.iloc[-1])-float(d.Close.iloc[-1]))<1e-6)
nd=pd.to_datetime(['2026-08-10'])
db.upsert('sbin',pd.DataFrame({'Open':[1000.],'High':[1010.],'Low':[990.],'Close':[1005.],'Volume':[1e6]},index=nd),'quote',0)
check("provisional accepted for new day", str(db.load('sbin').index[-1].date())=='2026-08-10')
db.upsert('sbin',pd.DataFrame({'Open':[1001.],'High':[1012.],'Low':[991.],'Close':[1008.],'Volume':[2e6]},index=nd),'dhan',1)
check("settled overwrites provisional", abs(float(db.load('sbin').Close.iloc[-1])-1008.)<1e-6, str(db.load('sbin').Close.iloc[-1]))

print("\n4. Signal generation from DB data")
for s in ['infy','tcs','reliance','nifty 500']:
    db.upsert(s,dh.daily(s,'2025-01-01','2026-08-07'),'dhan',1)
p=engine.prep(db.load('sbin'))
check("indicators computed", p is not None and {'rsi2','smaT','smaX','signal'}<=set(p.columns))
check("rsi2 in range", bool(p.rsi2.dropna().between(0,100).all()))
check("signal boolean", p.signal.dtype==bool)
check("signal implies rules", bool(((~p.signal)|((p.Close>p.smaT)&(p.rsi2<C.BUY_BELOW))).all()))

print("\n5. Freshness guard")
from live.trader import assert_fresh
px={'sbin':db.load('sbin')}; old={'sbin':db.load('sbin').iloc[:-40]}
assert_fresh(px,require=False)
try:
    assert_fresh(old,require=True); check("stale aborts",False)
except SystemExit: check("stale aborts",True)
check("stale passes with allow-stale", assert_fresh(old,require=False) is False)

print("\n6. Order construction (dry run)")
o=dh.place('sbin','BUY',114,'LIMIT',1102.5,tag='e_sbin'); b=o['body']
check("dry run flagged", o.get('dryRun') is True)
check("side/qty/price", b['transactionType']=='BUY' and b['quantity']==114 and b['price']==1102.5)
check("LIMIT not MARKET", b['orderType']=='LIMIT')
check("CNC delivery", b['productType']=='CNC')
check("securityId resolved", b['securityId']=='3045')

print("\n7. Arming / paper-mode logic")
import argparse
from live.trader import armed
for kw,arm,want in [(dict(live=False,paper=False),None,False),(dict(live=True,paper=False),None,False),
                    (dict(live=True,paper=False),'YES',True),(dict(live=True,paper=True),'YES',False)]:
    os.environ.pop('MRV5_ARM',None)
    if arm: os.environ['MRV5_ARM']=arm
    check(f"live={kw['live']} paper={kw['paper']} ARM={arm}", armed(argparse.Namespace(**kw))==want)

print("\n8. Account endpoints")
check("funds", dh.funds().get('availabelBalance')==1250000.0)
check("positions", dh.positions()[0]['netQty']==114)
check("orders", dh.orders()[0]['orderStatus']=='TRADED')

print("\n"+"="*58)
print(f"  {'ALL PASSED' if not FAIL else 'FAILURES: '+', '.join(FAIL)}")
print("="*58)
os.remove('/tmp/test_prices.db')
sys.exit(1 if FAIL else 0)
