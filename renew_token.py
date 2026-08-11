#!/usr/bin/env python3
"""Renew the Dhan access token and store it in R2 (single source of truth).

Reads the current token from R2, calls Dhan's RenewToken (extends validity by
24h), writes the new token back to R2. Run on a schedule (every ~12h) so the
token never expires and never needs manual rotation. The token must still be
active — if the chain breaks (system down > 24h), seed a fresh token once.
"""
import os, sys, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live import r2sync  # light: only os/sqlite at import time


def main():
    cur = r2sync.read_remote_token() or os.environ.get('DHAN_ACCESS_TOKEN')
    cid = os.environ.get('DHAN_CLIENT_ID')
    if not cur:
        raise SystemExit("no current token in R2 or env to renew — seed one first")
    if not cid:
        raise SystemExit("DHAN_CLIENT_ID not set")
    r = requests.post('https://api.dhan.co/v2/RenewToken',
                      headers={'access-token': cur, 'dhanClientId': cid,
                               'client-id': cid, 'Content-Type': 'application/json',
                               'Accept': 'application/json'}, timeout=30)
    if not r.ok:
        raise SystemExit(f"RenewToken failed {r.status_code}: {r.text[:300]}")
    j = r.json() if r.text else {}
    # be tolerant about the field name Dhan returns
    new = (j.get('accessToken') or j.get('access_token') or j.get('token')
           or (j.get('data') or {}).get('accessToken'))
    if not new:
        raise SystemExit(f"no token field in RenewToken response: {str(j)[:300]}")
    if r2sync.write_remote_token(new):
        print(f"renewed + stored new token in R2 (len {len(new)})")
    else:
        raise SystemExit("renewed but failed to write token to R2")


if __name__ == '__main__':
    main()
