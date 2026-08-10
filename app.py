#!/usr/bin/env python3
"""MR-v5 live dashboard. Three tabs: Analytics, Positions, Portfolio.

Serves instantly from out/results.json (written by backtest.py). Never blocks
a page load on a simulation.

  GET  /                     dashboard
  GET  /api/analytics        backtest + forward test metrics
  GET  /api/positions        open book, live marks, new signals
  GET  /api/portfolio        equity curve of trades actually taken
  POST /api/run              recompute the backtest in the background
  GET  /api/status           job state
  POST /api/ingest           in-process EOD refresh (disabled on free — OOMs)
  GET  /api/ingest/status    ingest job state
  POST /api/reload           re-pull DB/results from R2 (token-gated, cheap)
  GET  /api/reload/status    reload job state
  GET  /health
"""
import os, json, sys, subprocess, threading, datetime, traceback
from flask import Flask, request, jsonify, Response, send_file
import pandas as pd

from mrv5 import env as _env  # loads .env
app = Flask(__name__)
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'out')
JOB = {'status': 'idle', 'log': [], 'started': None}
INGEST = {'status': 'idle', 'log': [], 'started': None}
RELOAD = {'status': 'idle', 'pulled': [], 'error': None}

# free-tier persistence: pull DB/results from Cloudflare R2 on boot (no-op if unset)
try:
    from live import r2sync
    if r2sync.enabled():
        print("r2 sync enabled — pulling state on boot", flush=True)
        r2sync.download()
except Exception as _e:
    print(f"r2 boot download error: {_e}", flush=True)

def log(m):
    JOB['log'] = (JOB['log'] + [f"{datetime.datetime.now():%H:%M:%S}  {m}"])[-120:]
    print(m, flush=True)

def _r2_push():
    """Persist DB/results to R2 after a write. No-op if R2 is not configured."""
    try:
        from live import r2sync
        if r2sync.enabled():
            r2sync.upload()
    except Exception as e:
        print(f"r2 push error: {e}", flush=True)

# ---------------------------------------------------------------- analytics
@app.route('/api/analytics')
def api_analytics():
    from mrv5 import results
    r = results.load()
    if not r:
        return jsonify(empty=True, message="No results yet. Run a backtest."), 200
    return jsonify(r)

# ---------------------------------------------------------------- positions
@app.route('/api/positions')
def api_positions():
    try:
        from live import portfolio, db
        pos = portfolio.open_positions()
        sigs, free = portfolio.new_signals()
        cov = db.coverage()
        return jsonify(positions=pos, signals=sigs, free_slots=free,
                       data_as_of=cov.get('mx'), db_symbols=cov.get('syms'),
                       generated=str(datetime.datetime.now())[:19])
    except Exception as e:
        return jsonify(error=str(e), positions=[], signals=[]), 200

# ---------------------------------------------------------------- portfolio
@app.route('/api/portfolio')
def api_portfolio():
    try:
        from live import portfolio
        return jsonify(portfolio.portfolio_state())
    except Exception as e:
        return jsonify(error=str(e)), 200

# ---------------------------------------------------------------- run
def _run(overrides):
    from mrv5 import config as C, data, engine, report, dashboard, results
    try:
        JOB['status'] = 'running'; JOB['log'] = []
        JOB['started'] = str(datetime.datetime.now())[:19]
        for k, v in (overrides or {}).items():
            if hasattr(C, k.upper()):
                cur = getattr(C, k.upper())
                setattr(C, k.upper(), v if isinstance(cur, bool) else type(cur)(v))
                log(f"override {k.upper()} = {v}")
        log("building universe...")
        syms = data.build_universe(C.UNIVERSE_SIZE, C.TURNOVER_LOOKBACK)
        prepped = {}
        for s in syms:
            d = engine.prep(data.load(s))
            if d is not None: prepped[s] = d
        log(f"{len(prepped)} symbols")
        cal, sy, M = engine.to_matrices(prepped, C.START, C.END)
        idx = data.load(C.HEDGE_INDEX); ci = pd.DatetimeIndex(cal)
        ixs = idx.Close.reindex(ci).ffill().bfill()
        below = (ixs < ixs.rolling(C.HEDGE_INDEX_SMA).mean()).values
        br = engine.breadth(M, cal).shift(1).fillna(0.5).values
        hedge = (below | (br < C.HEDGE_BREADTH_MIN)) if C.HEDGE_ON else None
        log("simulating...")
        E, T = engine.simulate(cal, sy, M, hedge_mask=hedge,
                               idx_ret=ixs.pct_change().fillna(0).values)
        k = report.metrics(E, T, C.CAPITAL)
        os.makedirs(OUT, exist_ok=True)
        E.to_csv(f'{OUT}/equity.csv'); T.to_csv(f'{OUT}/trades.csv', index=False)
        meta = dict(generated=str(datetime.datetime.now())[:16], universe=len(sy),
                    start=str(E.index[0].date()), end=str(E.index[-1].date()),
                    capital=C.CAPITAL, slots=C.SLOTS, cost=C.COST_ROUNDTRIP,
                    max_hold=C.MAX_HOLD_DAYS, buy_below=C.BUY_BELOW,
                    exit_above=C.EXIT_ABOVE, trend_sma=C.TREND_SMA,
                    stop_loss=str(C.USE_STOP_LOSS), hedge=str(C.HEDGE_ON))
        results.save(results.build(E, T, C.CAPITAL, ixs, meta))
        dashboard.render(dashboard.build_payload(E, T, C.CAPITAL, ixs, meta), f'{OUT}/dashboard.html')
        JOB['status'] = 'done'
        log(f"DONE  CAGR {k['cagr']:.1f}%  Sharpe {k['sharpe']:.2f}  {k['trades']} trades")
        _r2_push()
    except Exception as e:
        JOB['status'] = 'error'; log("ERROR " + str(e)); log(traceback.format_exc()[-1200:])

@app.route('/api/run', methods=['POST'])
def api_run():
    if JOB['status'] == 'running':
        return jsonify(error='already running'), 409
    threading.Thread(target=_run, args=(request.get_json(silent=True) or {},), daemon=True).start()
    return jsonify(ok=True)

@app.route('/api/status')
def api_status(): return jsonify(**JOB)

# ---------------------------------------------------------------- ingest (EOD)
def _ingest():
    """Daily EOD refresh: append settled bars, then recompute tomorrow's plan.
    Runs the exact steps the old cron ran. Never places orders (eod = plan only)."""
    def ilog(m):
        INGEST['log'] = (INGEST['log'] + [f"{datetime.datetime.now():%H:%M:%S}  {m}"])[-200:]
        print(m, flush=True)
    steps = [
        [sys.executable, '-m', 'live.ingest', '--mode', 'daily'],
        [sys.executable, '-m', 'live.trader', '--mode', 'eod'],
    ]
    try:
        INGEST['status'] = 'running'; INGEST['log'] = []
        INGEST['started'] = str(datetime.datetime.now())[:19]
        for cmd in steps:
            ilog('$ ' + ' '.join(cmd[2:]))
            p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=3000)
            for line in (p.stdout or '').splitlines()[-40:]:
                ilog(line)
            if p.returncode != 0:
                for line in (p.stderr or '').splitlines()[-20:]:
                    ilog('! ' + line)
                raise RuntimeError(f"{cmd[3:]} exited {p.returncode}")
        _r2_push(); ilog('r2 push done')
        INGEST['status'] = 'done'; ilog('DONE')
    except Exception as e:
        INGEST['status'] = 'error'; ilog('ERROR ' + str(e))

def _auth(req):
    """True if the request carries the INGEST_TOKEN (Bearer header or ?token=)."""
    tok = os.environ.get('INGEST_TOKEN')
    if not tok:
        return None  # not configured
    given = req.headers.get('Authorization', '').removeprefix('Bearer ').strip() \
            or req.args.get('token', '')
    return given == tok

@app.route('/api/ingest', methods=['POST'])
def api_ingest():
    # The full ingest+plan loads the universe into pandas (~hundreds of MB) and
    # OOM-kills a 512Mi free instance. The EOD job runs in GitHub Actions instead
    # (see .github/workflows/eod.yml), which uploads to R2, then calls /api/reload.
    # Only allow in-process ingest where memory is not a constraint.
    if os.environ.get('ALLOW_INPROCESS_INGEST', '').lower() not in ('1', 'yes', 'true'):
        return jsonify(error='in-process ingest disabled (would OOM on free tier); '
                             'the EOD job runs in GitHub Actions and calls /api/reload'), 501
    ok = _auth(request)
    if ok is None:
        return jsonify(error='INGEST_TOKEN not configured'), 503
    if not ok:
        return jsonify(error='unauthorized'), 401
    if INGEST['status'] == 'running':
        return jsonify(error='already running'), 409
    threading.Thread(target=_ingest, daemon=True).start()
    return jsonify(ok=True)

@app.route('/api/ingest/status')
def api_ingest_status(): return jsonify(**INGEST)

# ------------------------------------------------ reload (pull fresh state from R2)
def _reload():
    """Re-download DB/results from R2 into the running instance. Cheap (file
    copy, no pandas) so it is safe on a 512Mi free instance. Called by the
    GitHub Actions EOD job after it uploads a refreshed DB."""
    try:
        RELOAD['status'] = 'running'
        from live import r2sync
        got = r2sync.download() if r2sync.enabled() else []
        RELOAD['status'] = 'done'; RELOAD['pulled'] = got
        print(f"reload pulled: {got}", flush=True)
    except Exception as e:
        RELOAD['status'] = 'error'; RELOAD['error'] = str(e)
        print(f"reload error: {e}", flush=True)

@app.route('/api/reload', methods=['POST'])
def api_reload():
    ok = _auth(request)
    if ok is None:
        return jsonify(error='INGEST_TOKEN not configured'), 503
    if not ok:
        return jsonify(error='unauthorized'), 401
    if RELOAD['status'] == 'running':
        return jsonify(error='already running'), 409
    threading.Thread(target=_reload, daemon=True).start()
    return jsonify(ok=True)

@app.route('/api/reload/status')
def api_reload_status(): return jsonify(**RELOAD)

@app.route('/health')
def health():
    from mrv5 import results
    return jsonify(ok=True, job=JOB['status'], has_results=results.load() is not None)

@app.route('/download/<name>')
def download(name):
    if name not in ('equity.csv', 'trades.csv', 'results.json', 'dashboard.html'):
        return "not allowed", 400
    p = os.path.join(OUT, name)
    return send_file(p, as_attachment=True) if os.path.exists(p) else ("not found", 404)

@app.route('/')
def home():
    return send_file(os.path.join(ROOT, 'templates', 'dashboard.html'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), threaded=True)
