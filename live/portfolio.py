"""Live/paper portfolio state: open positions with live marks, and the realised
equity curve of trades actually taken (as opposed to backtested)."""
import os, sys, time, datetime
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mrv5 import config as C, engine
from live import db, state

def _last_close(sym):
    d = db.load(sym)
    if d is None or len(d) == 0: return None, None
    return float(d.Close.iloc[-1]), str(d.index[-1].date())

# live-quote cache: avoid hammering Dhan on every 10s poll, and back off for
# 5 min after a failure (e.g. expired token) so it never slows the response.
_LTP = {'t': 0.0, 'px': {}, 'cooldown_until': 0.0}
_IDMAP = None

# Yahoo Finance delayed-quote cache (NSE, ~15-min delay). Refreshed every 5 min.
_YF = {'t': 0.0, 'px': {}}


def yahoo_quotes(symbols):
    """15-min-delayed NSE prices from Yahoo Finance. Used as fallback when the
    Dhan token is expired. Returns {} on any error so callers still fall back to EOD."""
    if not symbols:
        return {}
    now = time.time()
    if now - _YF['t'] < 300 and _YF['px']:
        return {s: _YF['px'][s] for s in symbols if s in _YF['px']}
    try:
        import yfinance as yf
        tickers = [s.upper() + '.NS' for s in symbols]
        data = yf.download(tickers, period='1d', interval='1m',
                           progress=False, auto_adjust=True, threads=False)
        px = {}
        if not data.empty:
            closes = data['Close'] if 'Close' in data.columns else data.xs('Close', axis=1, level=0)
            last = closes.iloc[-1]
            for s, t in zip(symbols, tickers):
                v = last.get(t) if hasattr(last, 'get') else (last[t] if t in last.index else None)
                if v is not None and not (isinstance(v, float) and v != v):  # skip NaN
                    px[s] = float(v)
        _YF.update(t=now, px=px)
        return {s: px[s] for s in symbols if s in px}
    except Exception as e:
        print(f"yahoo_quotes error: {str(e)[:120]}", flush=True)
        return {}

def _security_ids():
    """Small precomputed {symbol: securityId} map (universe). Loaded once. NEVER
    downloads the multi-MB Dhan scrip master (that OOMs the free web instance)."""
    global _IDMAP
    if _IDMAP is None:
        _IDMAP = {}
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        import json
        for fn in ('security_ids.json', 'symbol_map.json'):
            try:
                for k, v in json.load(open(os.path.join(root, 'live', fn))).items():
                    if not k.startswith('_') and v:
                        _IDMAP[k.strip().lower()] = str(v)
            except Exception:
                pass
    return _IDMAP

def live_quotes(symbols):
    """Live LTP from Dhan for the given symbols, cached ~25s. Returns {} if the
    Data API is unavailable (token expired etc.) — callers fall back to EOD.
    Resolves ids from the small map only; never touches the scrip master."""
    if not symbols:
        return {}
    now = time.time()
    if now < _LTP['cooldown_until']:
        return {}
    if now - _LTP['t'] < 8 and _LTP['px']:
        return _LTP['px']
    idmap = _security_ids()
    id_to_sym = {idmap[s.lower()]: s for s in symbols if s.lower() in idmap}
    if not id_to_sym:
        return {}
    try:
        from live.dhan import Dhan
        px = Dhan(dry_run=True).ltp_ids(id_to_sym)
        _LTP.update(t=now, px=px, cooldown_until=0.0)
        return px
    except Exception as e:
        _LTP.update(cooldown_until=now + 300, px={})
        print(f"Dhan unavailable, trying Yahoo Finance (15-min delay): {str(e)[:80]}", flush=True)
        return yahoo_quotes(symbols)

def open_positions():
    """Open book with live marks and the current signal state of each holding."""
    book = state.open_positions()
    lq = live_quotes([p['symbol'] for p in book])
    rows = []
    for p in book:
        s = p['symbol']
        px, asof = _last_close(s)
        lpx = lq.get(s)
        is_live = lpx is not None
        if is_live:
            px, asof = lpx, 'live'
        d = db.load(s)
        rsi2 = smaX = trend = None
        exit_flag, exit_reason = False, None
        if d is not None and len(d) > C.TREND_SMA:
            pr = engine.prep(d)
            if pr is not None:
                last = pr.iloc[-1]
                rsi2 = float(last.rsi2); smaX = float(last.smaX)
                trend = bool(last.Close > last.smaT)
                age = (datetime.date.today() - datetime.date.fromisoformat(p['entry_date'])).days
                if rsi2 > C.EXIT_ABOVE:            exit_flag, exit_reason = True, 'RSI2 > %.0f' % C.EXIT_ABOVE
                elif last.Close > last.smaX:       exit_flag, exit_reason = True, 'close > SMA5'
                elif age >= C.MAX_HOLD_DAYS:       exit_flag, exit_reason = True, 'time stop %dd' % C.MAX_HOLD_DAYS
        ep = float(p['entry_px']); qty = int(p['qty'])
        age = (datetime.date.today() - datetime.date.fromisoformat(p['entry_date'])).days
        chg = ((px-ep)/ep*100) if px else None
        rows.append(dict(symbol=s, qty=qty, entry_px=round(ep, 2),
            entry_date=p['entry_date'], days_held=age,
            last_px=round(px, 2) if px else None, as_of=asof,
            pct_change=round(chg, 2) if chg is not None else None,
            unrealised=round(qty*(px-ep), 0) if px else None,
            cost=round(qty*ep, 0), value=round(qty*px, 0) if px else None,
            rsi2=round(rsi2, 1) if rsi2 is not None else None,
            sma5=round(smaX, 2) if smaX is not None else None,
            above_trend=trend, exit_signal=exit_flag, exit_reason=exit_reason,
            days_to_timestop=max(C.MAX_HOLD_DAYS-age, 0), live=is_live))
    return sorted(rows, key=lambda x: -(x['pct_change'] or -999))

def new_signals(limit=25):
    """Today's fresh entry candidates, deepest RSI(2) first."""
    held = {p['symbol'] for p in state.open_positions()}
    out = []
    for s in db.symbols():
        if s in held or ' ' in s: continue
        d = db.load(s)
        if d is None or len(d) < C.MIN_HISTORY: continue
        pr = engine.prep(d)
        if pr is None or not bool(pr.iloc[-1].signal): continue
        last = pr.iloc[-1]
        out.append(dict(symbol=s, rsi2=round(float(last.rsi2), 2),
            close=round(float(last.Close), 2), as_of=str(pr.index[-1].date()),
            sma200=round(float(last.smaT), 2),
            pct_above_trend=round(float((last.Close/last.smaT-1)*100), 2)))
    out.sort(key=lambda x: x['rsi2'])
    free = C.SLOTS - len(held)
    for i, o in enumerate(out): o['would_take'] = i < max(free, 0)
    return out[:limit], free

def signals_from_plan():
    """New entry signals from the latest persisted plan (computed by the EOD /
    live-now workflow). Light — reads one row from live.db, no universe rescan.
    Avoids the multi-second 252-symbol recompute that overwhelmed the free web
    instance."""
    import json
    held = state.open_positions()
    free = max(C.SLOTS - len(held), 0)
    c = state.conn()
    row = c.execute("SELECT payload FROM runs ORDER BY ts DESC LIMIT 1").fetchone()
    c.close()
    if not row:
        return [], free
    try:
        plan = json.loads(row['payload'])
    except Exception:
        return [], free
    out = []
    for e in plan.get('entries', []):
        limit_px = round(float(e.get('limit') or e.get('ref_price') or 0), 2)
        out.append(dict(symbol=e['symbol'], rsi2=round(float(e.get('rsi2', 0)), 2),
                        close=round(float(e.get('ref_price', 0)), 2),
                        qty=int(e.get('qty') or 0), limit_px=limit_px,
                        as_of=(plan.get('generated', '') or '')[:10],
                        sma200=None, pct_above_trend=None, would_take=True))
    return out, free

def realised_trades():
    """Trades actually executed, reconstructed from the journal."""
    c = state.conn()
    j = pd.read_sql_query("SELECT * FROM journal WHERE kind IN ('ENTRY','EXIT') ORDER BY ts", c)
    c.close()
    if len(j) == 0: return pd.DataFrame()
    trades, open_ = [], {}
    for _, r in j.iterrows():
        # FIFO queue per symbol: a symbol can round-trip more than once, and a
        # re-entry may be timestamped before the prior exit (open 09:15 vs close
        # 15:30 same day), so a single-slot overwrite would drop/mispair trades.
        if r['kind'] == 'ENTRY':
            open_.setdefault(r['symbol'], []).append(r)
        elif r['kind'] == 'EXIT' and open_.get(r['symbol']):
            e = open_[r['symbol']].pop(0)
            qty = int(e['qty'] or 0); ep = float(e['price'] or 0); xp = float(r['price'] or 0)
            if not (qty and ep): continue
            ed = pd.Timestamp(e['ts']); xd = pd.Timestamp(r['ts'])
            trades.append(dict(symbol=r['symbol'], entry_dt=ed, exit_dt=xd,
                entry_px=ep, exit_px=xp, qty=qty, notional=qty*ep,
                pnl=qty*(xp-ep) - qty*ep*C.COST_ROUNDTRIP,
                ret=(xp-ep)/ep*100 - C.COST_ROUNDTRIP*100,
                hold=max((xd-ed).days, 0), typ=r['note'] or 'exit'))
    return pd.DataFrame(trades)

def closed_trades_list():
    """All closed trades (realised), newest-agnostic order, as the same 10-column
    rows the Portfolio tab uses. Powers the Positions tab's Closed Trades section."""
    T = realised_trades()
    if not len(T):
        return []
    T = T.sort_values('exit_dt')
    return [[r.symbol, str(pd.Timestamp(r.entry_dt).date()), str(pd.Timestamp(r.exit_dt).date()),
             round(r.entry_px, 2), round(r.exit_px, 2), int(r.qty), round(r.pnl),
             round(r.ret, 2), int(r.hold), r.typ] for r in T.itertuples()]

def portfolio_state(capital=None):
    """Everything the Portfolio tab needs."""
    capital = capital or float(os.environ.get('MRV5_EQUITY', C.CAPITAL))
    pos = open_positions()
    T = realised_trades()
    realised = float(T.pnl.sum()) if len(T) else 0.0
    unreal = float(sum(p['unrealised'] or 0 for p in pos))
    deployed = float(sum(p['cost'] or 0 for p in pos))
    equity = capital + realised + unreal
    curve = []
    if len(T):
        t = T.sort_values('exit_dt')
        cum = capital
        for _, r in t.iterrows():
            cum += r.pnl
            curve.append([str(pd.Timestamp(r.exit_dt).date()), round(cum)])
    return dict(
        capital=round(capital), equity=round(equity),
        realised=round(realised), unrealised=round(unreal),
        deployed=round(deployed), cash=round(capital+realised-deployed),
        utilisation=round(deployed/max(equity, 1)*100, 1),
        open_count=len(pos), slots=C.SLOTS, free_slots=max(C.SLOTS-len(pos), 0),
        total_return=round((equity/capital-1)*100, 2),
        closed_trades=len(T),
        win_rate=round(float((T.pnl > 0).mean()*100), 1) if len(T) else None,
        avg_return=round(float(T.ret.mean()), 2) if len(T) else None,
        avg_pnl=round(float(T.pnl.mean())) if len(T) else None,
        best=round(float(T.ret.max()), 2) if len(T) else None,
        worst=round(float(T.ret.min()), 2) if len(T) else None,
        avg_hold=round(float(T.hold.mean()), 1) if len(T) else None,
        profit_factor=round(float(T.pnl[T.pnl > 0].sum()/abs(T.pnl[T.pnl <= 0].sum())), 3)
            if len(T) and (T.pnl <= 0).any() else None,
        curve=curve, positions=pos,
        trades=[[r.symbol, str(pd.Timestamp(r.entry_dt).date()), str(pd.Timestamp(r.exit_dt).date()),
                 round(r.entry_px, 2), round(r.exit_px, 2), int(r.qty), round(r.pnl),
                 round(r.ret, 2), int(r.hold), r.typ] for r in T.itertuples()] if len(T) else [])
