"""Local price database. One SQLite file, grows daily, serves both live and backtest.

    bars(symbol, date, open, high, low, close, volume, source, final)
      final=1 -> settled EOD bar. final=0 -> provisional intraday snapshot,
      overwritten by the settled bar on the next ingest.
"""
import os, sqlite3, datetime
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from mrv5 import env as _env  # loads .env into os.environ

import pandas as pd, numpy as np

DB_PATH = os.environ.get('MRV5_DB') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'prices.db')

def conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS bars(
        symbol TEXT NOT NULL, date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        source TEXT, final INTEGER DEFAULT 1,
        PRIMARY KEY(symbol, date))""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_bars_date ON bars(date)")
    c.execute("""CREATE TABLE IF NOT EXISTS ingest_log(
        ts TEXT, trade_date TEXT, symbols INTEGER, rows INTEGER,
        source TEXT, note TEXT)""")
    c.commit()
    return c

def upsert(symbol, df, source='dhan', final=1):
    """df indexed by date with Open/High/Low/Close/Volume. Returns rows written.
    A settled bar (final=1) always overwrites a provisional one; a provisional
    bar never overwrites a settled one."""
    if df is None or len(df) == 0: return 0
    c = conn(); n = 0
    for dt, r in df.iterrows():
        d = str(pd.Timestamp(dt).date())
        if not final:
            ex = c.execute("SELECT final FROM bars WHERE symbol=? AND date=?", (symbol, d)).fetchone()
            if ex and ex['final'] == 1: continue
        c.execute("""INSERT INTO bars(symbol,date,open,high,low,close,volume,source,final)
                     VALUES(?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(symbol,date) DO UPDATE SET
                       open=excluded.open, high=excluded.high, low=excluded.low,
                       close=excluded.close, volume=excluded.volume,
                       source=excluded.source, final=excluded.final""",
                  (symbol, d, _f(r.get('Open')), _f(r.get('High')), _f(r.get('Low')),
                   _f(r.get('Close')), _f(r.get('Volume')), source, int(final)))
        n += 1
    c.commit(); c.close()
    return n

def _f(v):
    try:
        v = float(v); return v if np.isfinite(v) else None
    except Exception: return None

def load(symbol, start=None, end=None):
    c = conn()
    q = "SELECT date,open,high,low,close,volume FROM bars WHERE symbol=?"
    p = [symbol]
    if start: q += " AND date>=?"; p.append(str(start))
    if end:   q += " AND date<=?"; p.append(str(end))
    df = pd.read_sql_query(q + " ORDER BY date", c, params=p, parse_dates=['date'])
    c.close()
    if len(df) == 0: return None
    df = df.set_index('date')
    df.columns = ['Open','High','Low','Close','Volume']
    return df[(df.Close > 0) & (df.High >= df.Low)]

def last_date(symbol=None):
    c = conn()
    r = (c.execute("SELECT MAX(date) d FROM bars WHERE symbol=?", (symbol,)).fetchone()
         if symbol else c.execute("SELECT MAX(date) d FROM bars").fetchone())
    c.close(); return r['d']

def symbols():
    c = conn(); r = [x[0] for x in c.execute("SELECT DISTINCT symbol FROM bars ORDER BY symbol")]
    c.close(); return r

def coverage():
    c = conn()
    r = c.execute("""SELECT COUNT(DISTINCT symbol) syms, COUNT(*) rows,
                     MIN(date) mn, MAX(date) mx,
                     SUM(final=0) provisional FROM bars""").fetchone()
    out = dict(r); c.close(); return out

def stale(symbols_, asof):
    """Symbols whose newest bar is older than asof."""
    c = conn()
    have = {r['symbol']: r['d'] for r in
            c.execute("SELECT symbol, MAX(date) d FROM bars GROUP BY symbol")}
    c.close()
    return [s for s in symbols_ if have.get(s, '0000') < str(asof)]

def log_ingest(trade_date, syms, rows, source, note=''):
    c = conn()
    c.execute("INSERT INTO ingest_log VALUES(?,?,?,?,?,?)",
              (datetime.datetime.now().isoformat()[:19], str(trade_date), syms, rows, source, note))
    c.commit(); c.close()
