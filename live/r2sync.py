#!/usr/bin/env python3
"""Cloudflare R2 (S3-compatible) sync — persistence for the free-tier deploy.

Render's free plan has no persistent disk, so the SQLite files and results are
wiped on every restart. On boot we pull them from R2; after each ingest or
backtest we push them back. Entirely a no-op unless the R2_* env vars are set,
so local dev and paid-disk deploys are unaffected.

    python -m live.r2sync upload      # seed the bucket from local files
    python -m live.r2sync download    # pull the bucket to local
    python -m live.r2sync status      # show config + targets

Required env: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
"""
import os, sqlite3

_REQUIRED = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")


def enabled():
    return all(os.environ.get(k) for k in _REQUIRED)


def _targets():
    """(local_path, object_key, is_sqlite) for each file we persist."""
    from live import db, state
    from mrv5 import results
    return [
        (db.DB_PATH, "prices.db", True),
        (state.DB, "live.db", True),
        (results.PATH, "results.json", False),
    ]


def _client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _checkpoint(path):
    """Fold the WAL back into the main .db file so a single-file upload is complete."""
    if not os.path.exists(path):
        return
    try:
        c = sqlite3.connect(path, timeout=60)
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
    except Exception as e:
        print(f"r2 checkpoint {path}: {e}", flush=True)


def download():
    if not enabled():
        return []
    c, b, got = _client(), os.environ["R2_BUCKET"], []
    for path, key, _ in _targets():
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            c.download_file(b, key, path)
            got.append(key)
        except Exception as e:
            print(f"r2 download skip {key}: {e}", flush=True)
    print(f"r2 downloaded: {got}", flush=True)
    return got


def upload():
    if not enabled():
        return []
    c, b, put = _client(), os.environ["R2_BUCKET"], []
    for path, key, is_sqlite in _targets():
        if not os.path.exists(path):
            continue
        if is_sqlite:
            _checkpoint(path)
        try:
            c.upload_file(path, b, key)
            put.append(key)
        except Exception as e:
            print(f"r2 upload fail {key}: {e}", flush=True)
    print(f"r2 uploaded: {put}", flush=True)
    return put


TOKEN_KEY = "dhan_token.txt"

def read_remote_token():
    """Current Dhan access token stored in R2 (single source of truth). Returns
    the token string, or None if unset/unavailable. Never raises."""
    if not enabled():
        return None
    try:
        obj = _client().get_object(Bucket=os.environ["R2_BUCKET"], Key=TOKEN_KEY)
        tok = obj["Body"].read().decode().strip()
        return tok or None
    except Exception as e:
        print(f"r2 token read skip: {str(e)[:120]}", flush=True)
        return None

def write_remote_token(tok):
    """Store a new Dhan access token in R2. Returns True on success."""
    if not enabled() or not tok:
        return False
    try:
        _client().put_object(Bucket=os.environ["R2_BUCKET"], Key=TOKEN_KEY,
                             Body=tok.strip().encode())
        return True
    except Exception as e:
        print(f"r2 token write failed: {str(e)[:120]}", flush=True)
        return False


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "upload":
        upload()
    elif cmd == "download":
        download()
    else:
        print(f"enabled: {enabled()}")
        for path, key, is_sqlite in _targets():
            print(f"  {key:<14} <- {path}  (exists={os.path.exists(path)})")
