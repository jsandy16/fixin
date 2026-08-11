"""Dhan broker adapter — DhanHQ v2 REST. Thin, explicit, no magic.

Auth: set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in the environment.
Docs: https://dhanhq.co/docs/v2/
"""
import os, io, time, requests, pandas as pd
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from mrv5 import env as _env  # loads .env into os.environ


BASE = "https://api.dhan.co/v2"
# Compact master has SEM_TRADING_SYMBOL (the real NSE ticker); the detailed one
# only had SYMBOL_NAME (company name) which matched almost nothing.
SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"

class DhanNotSubscribed(RuntimeError):
    """Account lacks the Data API subscription (DH-902)."""

class DhanAuthError(RuntimeError):
    """Credentials rejected / token expired."""

class Dhan:
    def __init__(self, client_id=None, access_token=None, dry_run=True):
        self.cid = client_id or os.environ.get('DHAN_CLIENT_ID')
        self.tok = access_token or os.environ.get('DHAN_ACCESS_TOKEN')
        self.dry = dry_run
        if not self.dry and not (self.cid and self.tok):
            raise RuntimeError("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN required for live mode")
        self._map = None

    # ---------- plumbing ----------
    def _h(self):
        return {'Content-Type':'application/json','Accept':'application/json',
                'access-token': self.tok or '', 'client-id': self.cid or ''}

    def _req(self, method, path, **kw):
        for attempt in range(3):
            try:
                r = requests.request(method, BASE+path, headers=self._h(), timeout=30, **kw)
                if r.status_code == 429:
                    time.sleep(2**attempt); continue
                r.raise_for_status()
                return r.json() if r.text else {}
            except requests.HTTPError as e:
                body = getattr(e.response, 'text', '')[:400]
                if 'DH-902' in body or 'not subscribed to Data API' in body:
                    raise DhanNotSubscribed(
                        "Dhan Data API not subscribed on this account (DH-902).\n"
                        "  Fix : Dhan web -> Profile -> DhanHQ APIs -> subscribe to Data APIs.\n"
                        "  Or  : skip it entirely and use --source mirror (free EOD data).")
                if 'DH-901' in body or 'Invalid' in body and 'token' in body.lower():
                    raise DhanAuthError(
                        "Dhan rejected the credentials (token may have expired — they last 30 days).\n"
                        "  Fix: regenerate the access token and update .env")
                if attempt == 2: raise RuntimeError(f"{method} {path} -> {e} {body}")
                time.sleep(1.5**attempt)
            except requests.RequestException:
                if attempt == 2: raise
                time.sleep(1.5**attempt)

    # ---------- instruments ----------
    def scrip_map(self, refresh=False):
        """symbol (lowercase) -> securityId for NSE equity.

        Order of resolution:
          1. live/symbol_map.json   (your own overrides — always wins)
          2. cached scrip_master.csv
          3. download from Dhan
        Dhan's master CSV column names have changed before, so detection is
        defensive and fails with a readable message rather than a KeyError."""
        if self._map is not None and not refresh:
            return self._map
        here = os.path.dirname(os.path.abspath(__file__))
        override = {}
        ov_path = os.path.join(here, 'symbol_map.json')
        if os.path.exists(ov_path):
            import json as _j
            override = {k.lower(): str(v) for k, v in _j.load(open(ov_path)).items()}

        cache = os.path.join(here, 'scrip_master.csv')
        df = None
        if os.path.exists(cache) and not refresh:
            df = pd.read_csv(cache, low_memory=False)
        else:
            try:
                txt = requests.get(SCRIP_MASTER, timeout=120).text
                df = pd.read_csv(io.StringIO(txt), low_memory=False)
                # sanity-check: a real master has many columns and thousands of rows.
                # A proxy/error page parses as a 1-column frame — never cache that.
                if df.shape[1] < 4 or len(df) < 100:
                    raise ValueError(f"response is not a scrip master "
                                     f"(shape {df.shape}); got: {txt[:120]}")
                df.to_csv(cache, index=False)
            except Exception as e:
                if override:
                    print(f"[dhan] scrip master unavailable ({e}); using symbol_map.json only")
                    self._map = override
                    return self._map
                raise RuntimeError(
                    f"Could not fetch Dhan scrip master ({e}). Either allow access to "
                    f"{SCRIP_MASTER}, place it at {cache}, or supply {ov_path} "
                    f'as {{"sbin": "3045", ...}}')

        if df is None or df.shape[1] < 4:
            if override:
                print("[dhan] scrip master unusable; using symbol_map.json only")
                self._map = override
                return self._map
            raise RuntimeError(f"scrip master unusable (shape {None if df is None else df.shape})")
        up = {c.upper().replace('_','').replace(' ',''): c for c in df.columns}
        def pick(*names):
            for n in names:
                if n in up: return up[n]
            return None
        sym  = pick('SEMTRADINGSYMBOL','TRADINGSYMBOL','SYMBOLNAME','UNDERLYINGSYMBOL',
                    'SEMCUSTOMSYMBOL','DISPLAYNAME')
        sid  = pick('SECURITYID','SEMSMSTSECURITYID')
        # Dhan's detailed CSV uses EXCH_ID -> EXCHID; the compact one uses
        # SEM_EXM_EXCH_ID -> SEMEXMEXCHID. Missing EXCHID was the original bug.
        seg  = pick('EXCHID','EXCHSEGMENT','EXCHANGESEGMENT','SEMEXMEXCHID','SEMSEGMENT','SEGMENT')
        inst = pick('INSTRUMENT','INSTRUMENTTYPE','INSTRUMENTNAME','SEMINSTRUMENTNAME')
        ser  = pick('SERIES','SEMSERIES')
        if not sym or not sid:
            if override:
                print("[dhan] unrecognised master layout; using symbol_map.json only")
                self._map = override
                return self._map
            raise RuntimeError(
                f"Unrecognised scrip-master layout. Columns seen: {list(df.columns)[:25]}. "
                f"Supply {ov_path} instead, or update pick() in live/dhan.py.")
        d = df
        # NSE only. 'NSE' (detailed) or 'E'/'NSE_EQ' (compact).
        if seg:
            v = d[seg].astype(str).str.upper()
            keep = v.str.contains('NSE', na=False) | v.isin(['E'])
            if keep.any(): d = d[keep]
        if inst:
            v = d[inst].astype(str).str.upper()
            keep = v.str.contains('EQUITY', na=False) | v.isin(['ES'])
            if keep.any(): d = d[keep]
        if ser:
            v = d[ser].astype(str).str.upper()
            keep = v.isin(['EQ','BE',''] ) | v.isna()
            if keep.any(): d = d[keep]
        if len(d) == 0:
            d = df   # filters wrong for this layout; fall back to unfiltered
        m = {}
        for a, b in zip(d[sym], d[sid]):
            if pd.notna(a) and pd.notna(b):
                m.setdefault(str(a).strip().lower(), str(b).strip().split('.')[0])
        m.update(override)
        if not m:
            raise RuntimeError("scrip master parsed to an empty map — check the CSV")
        print(f"[dhan] scrip map: {len(m):,} NSE equity symbols "
              f"(cols: {sym} / {sid}" + (f" / {seg}" if seg else "") + ")")
        self._map = m
        return self._map

    def diagnose_scrip(self, refresh=True, probe=('sbin','icicibank','hdfcbank','reliance','infy')):
        """Explain exactly what happened with the scrip master."""
        here = os.path.dirname(os.path.abspath(__file__))
        cache = os.path.join(here, 'scrip_master.csv')
        print(f"source : {SCRIP_MASTER}")
        print(f"cache  : {cache} {'(exists)' if os.path.exists(cache) else '(absent)'}")
        try:
            if refresh or not os.path.exists(cache):
                print("downloading...")
                txt = requests.get(SCRIP_MASTER, timeout=180).text
                df = pd.read_csv(io.StringIO(txt), low_memory=False)
                if df.shape[1] < 4 or len(df) < 100:
                    print(f"  BAD RESPONSE shape={df.shape}: {txt[:200]}")
                    return None
                df.to_csv(cache, index=False)
            else:
                df = pd.read_csv(cache, low_memory=False)
            print(f"rows={len(df):,} cols={df.shape[1]}")
            print(f"columns: {list(df.columns)}")
        except Exception as e:
            print(f"DOWNLOAD FAILED: {e}")
            return None
        self._map = None
        m = self.scrip_map(refresh=False)
        print(f"\nresolved {len(m):,} symbols")
        for p in probe:
            print(f"  {p:<14} -> {m.get(p.lower(), 'NOT FOUND')}")
        return m

    def security_id(self, symbol):
        sid = self.scrip_map().get(symbol.strip().lower())
        if sid is None:
            raise KeyError(f"no Dhan securityId for '{symbol}'. Add it to "
                           f"live/symbol_map.json as {{\"{symbol.lower()}\": \"<id>\"}}")
        return sid

    # ---------- market data ----------
    def daily(self, symbol, from_date, to_date):
        sid = self.security_id(symbol)
        j = self._req('POST', '/charts/historical', json=dict(
            securityId=sid, exchangeSegment='NSE_EQ', instrument='EQUITY',
            expiryCode=0, oi=False, fromDate=str(from_date), toDate=str(to_date)))
        if not j or 'close' not in j: return None
        d = pd.DataFrame({k: j[k] for k in ('open','high','low','close','volume') if k in j})
        ts = j.get('timestamp') or j.get('start_Time') or j.get('startTime')
        d.index = pd.to_datetime(pd.Series(ts), unit='s').dt.normalize()
        d.columns = [c.capitalize() for c in d.columns]
        return d

    def ltp(self, symbols, segment='NSE_EQ'):
        """Live last-traded price for a list of symbols -> {symbol: last_price}.
        Resolves security ids via the (large) scrip master — do NOT call this on
        a memory-constrained host; use ltp_ids() with a precomputed id map."""
        ids = {}
        for s in symbols:
            try:
                ids[str(self.security_id(s))] = s
            except KeyError:
                pass
        return self.ltp_ids(ids, segment)

    def ltp_ids(self, id_to_symbol, segment='NSE_EQ'):
        """LTP given a {security_id: symbol} map. Skips the scrip master entirely,
        so it is safe on the 512Mi web instance."""
        ids = {str(k): v for k, v in (id_to_symbol or {}).items() if k}
        if not ids:
            return {}
        j = self._req('POST', '/marketfeed/ltp', json={segment: [int(x) for x in ids]})
        out = {}
        data = ((j or {}).get('data') or {}).get(segment, {}) or {}
        for sid, info in data.items():
            sym = ids.get(str(sid))
            if sym and info:
                px = info.get('last_price', info.get('ltp'))
                if px:
                    out[sym] = float(px)
        return out

    # ---------- account ----------
    def funds(self):    return self._req('GET', '/fundlimit')
    def positions(self):return self._req('GET', '/positions') or []
    def holdings(self): return self._req('GET', '/holdings') or []
    def orders(self):   return self._req('GET', '/orders') or []

    # ---------- orders ----------
    def place(self, symbol, side, qty, order_type='LIMIT', price=None,
              product='CNC', validity='DAY', tag=None):
        """side: BUY | SELL. product CNC=delivery, INTRADAY=MIS.
        LIMIT is the default deliberately — market orders at the open on a
        250-name universe is how backtest edge dies."""
        sid = self.security_id(symbol)
        body = dict(dhanClientId=self.cid, transactionType=side.upper(),
                    exchangeSegment='NSE_EQ', productType=product,
                    orderType=order_type.upper(), validity=validity,
                    securityId=sid, quantity=int(qty), price=float(price or 0),
                    disclosedQuantity=0, afterMarketOrder=False)
        if tag: body['correlationId'] = str(tag)[:25]
        if self.dry:
            return {'dryRun': True, 'orderStatus':'DRY', 'body': body}
        return self._req('POST', '/orders', json=body)

    def cancel(self, order_id):
        if self.dry: return {'dryRun': True, 'orderId': order_id}
        return self._req('DELETE', f'/orders/{order_id}')
