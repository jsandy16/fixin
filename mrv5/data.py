"""Fetch + cache NSE daily OHLCV from the eod2_data mirror (free, official EOD)."""
import os, io, requests, pandas as pd, numpy as np
from . import env as _env  # loads .env into os.environ

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

GH_TOKEN = os.environ.get('GITHUB_TOKEN')
RAW = "https://raw.githubusercontent.com/BennyThadikaran/eod2_data/main/daily/{}.csv"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'cache')

def _get(sym, refresh=False):
    os.makedirs(CACHE, exist_ok=True)
    fp = os.path.join(CACHE, sym.replace('/','_') + ".csv")
    if os.path.exists(fp) and not refresh:
        try: return pd.read_csv(fp, parse_dates=['Date'])
        except Exception: pass
    try:
        r = requests.get(RAW.format(quote(sym)), timeout=30)
        if r.status_code != 200: return None
        d = pd.read_csv(io.StringIO(r.text), parse_dates=['Date'])
        d.to_csv(fp, index=False)
        return d
    except Exception:
        return None

def load_db(sym):
    """Read from the local SQLite price DB if it has this symbol."""
    try:
        from live import db as _db
        if not os.path.exists(_db.DB_PATH): return None
        return _db.load(sym)
    except Exception:
        return None

def load(sym, refresh=False, prefer_db=None):
    """Price loader. If the local DB exists it wins (that is the whole point of
    ingesting once). Set MRV5_USE_DB=0 to force the mirror."""
    if prefer_db is None:
        prefer_db = os.environ.get('MRV5_USE_DB', '1') == '1'
    if prefer_db and not refresh:
        d = load_db(sym)
        if d is not None and len(d) > 0: return d
    return _load_mirror(sym, refresh)

def _load_mirror(sym, refresh=False):
    d = _get(sym, refresh)
    if d is None or len(d) == 0: return None
    if 'Series' in d.columns:
        d = d[d.Series.isin(['EQ','BE']) | d.Series.isna()]
    keep = ['Date','Open','High','Low','Close'] + (['Volume'] if 'Volume' in d.columns else [])
    d = d[keep].dropna(subset=['Close']).drop_duplicates('Date').set_index('Date').sort_index()
    if 'Volume' not in d.columns: d['Volume'] = np.nan
    return d[(d.Close > 0) & (d.High >= d.Low)]

def symbol_list(refresh=False):
    """All tradable symbols in the mirror.

    Uses the git TREE api (one request) rather than the contents api
    (36 paginated requests, which trips GitHub's 60/hr unauthenticated limit
    and silently returns an empty list)."""
    fp = os.path.join(CACHE, '_symbols.txt')
    if os.path.exists(fp) and not refresh:
        names = [x for x in open(fp).read().split('\n') if x]
        if names: return names
    os.makedirs(CACHE, exist_ok=True)
    names = []
    # 0. bundled list shipped with the package — works offline, no API call
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'symbols_bundled.txt')
    if os.path.exists(bundled) and not refresh:
        names = [x.strip() for x in open(bundled) if x.strip()]
        if names:
            open(fp, 'w').write('\n'.join(names))
            return names
    # 1. git tree api — single call, no pagination
    try:
        r = requests.get(
            "https://api.github.com/repos/BennyThadikaran/eod2_data/git/trees/main",
            params={'recursive': '1'}, timeout=90,
            headers={'Authorization': f'Bearer {GH_TOKEN}'} if GH_TOKEN else {})
        j = r.json()
        if isinstance(j, dict) and j.get('tree'):
            names = [t['path'][6:-4] for t in j['tree']
                     if t['path'].startswith('daily/') and t['path'].endswith('.csv')]
        elif isinstance(j, dict) and 'message' in j:
            print(f"[data] github tree api: {j['message'][:90]}")
    except Exception as e:
        print(f"[data] tree api failed: {e}")
    # 2. fallback — paginated contents api
    if not names:
        try:
            page = 1
            while page <= 40:
                r = requests.get(
                    "https://api.github.com/repos/BennyThadikaran/eod2_data/contents/daily",
                    params={'per_page': 100, 'page': page}, timeout=60)
                j = r.json()
                if not isinstance(j, list) or not j: break
                names += [x['name'][:-4] for x in j if x['name'].endswith('.csv')]
                if len(j) < 100: break
                page += 1
        except Exception as e:
            print(f"[data] contents api failed: {e}")
    # 3. last resort — whatever is already cached on disk
    if not names:
        import glob as _g
        names = [os.path.basename(f)[:-4] for f in _g.glob(os.path.join(CACHE, '*.csv'))
                 if not os.path.basename(f).startswith('_')]
        if names:
            print(f"[data] using {len(names)} cached symbols (network unavailable)")
    if not names:
        raise RuntimeError(
            "Could not retrieve the symbol list. GitHub's API may be rate-limited "
            "(60 req/hr unauthenticated) — wait an hour, set a GITHUB_TOKEN, or "
            f"drop a newline-separated symbol list at {fp}")
    open(fp, 'w').write('\n'.join(names))
    return names

def build_universe(size, lookback, workers=16, refresh=False):
    """Rank by median daily turnover. WARNING: screened on TODAY's liquidity,
    so it carries survivorship bias. See README section 'Known biases'."""
    try:
        from live import db as _db
        if os.path.exists(_db.DB_PATH):
            dbs = _db.symbols()
            if len(dbs) >= 20:
                print(f"[data] universe from local DB ({len(dbs)} symbols)")
                ranked = []
                for s in dbs:
                    if ' ' in s: continue
                    d = _db.load(s)
                    if d is None or len(d) < lookback*0.9: continue
                    t = d.tail(lookback)
                    v = float((t.Close*t.Volume).median())
                    if np.isfinite(v): ranked.append((s, v))
                ranked.sort(key=lambda x: -x[1])
                if len(ranked) >= size*0.5:
                    return [s for s,_ in ranked[:size]]
    except Exception as e:
        print(f"[data] DB universe unavailable ({e}); using mirror")
    syms = [s for s in symbol_list(refresh) if s and ' ' not in s and
            not any(k in s.lower() for k in
            ['nifty','bees','etf','gold','liquid','bond','gsec','g-sec','index'])]
    out = []
    def job(s):
        d = load(s, refresh)
        if d is None or len(d) < lookback * 0.9: return None
        t = d.tail(lookback)
        v = float((t.Close * t.Volume).median())
        return (s, v) if np.isfinite(v) else None
    with ThreadPoolExecutor(workers) as ex:
        for r in ex.map(job, syms):
            if r: out.append(r)
    out.sort(key=lambda x: -x[1])
    return [s for s, _ in out[:size]]
