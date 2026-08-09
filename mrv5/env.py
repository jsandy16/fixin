"""Load a .env file into os.environ. No dependency; called automatically on import.

Precedence: real environment variables always win over .env, so a systemd unit
or CI secret can override the file without editing it.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.environ.get('MRV5_ENV_FILE') or os.path.join(ROOT, '.env')

def load(path=None, override=False):
    p = path or ENV_PATH
    if not os.path.exists(p): return {}
    seen = {}
    with open(p) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            if line.lower().startswith('export '): line = line[7:].strip()
            k, v = line.split('=', 1)
            k = k.strip(); v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"): v = v[1:-1]
            seen[k] = v
            if override or k not in os.environ: os.environ[k] = v
    return seen

def check(need_dhan=False):
    """Report what's configured. Never prints a secret in full."""
    def mask(v):
        if not v: return None
        return v[:4] + '…' + v[-4:] if len(v) > 12 else '…' * len(v)
    rows = [
        ('DHAN_CLIENT_ID',     os.environ.get('DHAN_CLIENT_ID'),     need_dhan),
        ('DHAN_ACCESS_TOKEN',  mask(os.environ.get('DHAN_ACCESS_TOKEN')), need_dhan),
        ('MRV5_EQUITY',        os.environ.get('MRV5_EQUITY'),        False),
        ('MRV5_MAX_ORDER',     os.environ.get('MRV5_MAX_ORDER'),     False),
        ('MRV5_MAX_ORDERS',    os.environ.get('MRV5_MAX_ORDERS'),    False),
        ('MRV5_ARM',           os.environ.get('MRV5_ARM'),           False),
        ('MRV5_DB',            os.environ.get('MRV5_DB'),            False),
    ]
    print(f"env file: {ENV_PATH} {'(found)' if os.path.exists(ENV_PATH) else '(NOT FOUND)'}")
    missing = []
    for k, v, req in rows:
        status = v if v else ('MISSING' if req else 'default')
        print(f"  {k:<20} {status}")
        if req and not v: missing.append(k)
    if missing:
        print(f"\n  Missing required: {', '.join(missing)}")
        print(f"  Add them to {ENV_PATH} — see .env.example")
    return not missing

load()
