"""Durable position/order state. SQLite so a crash never loses the book."""
import sqlite3, os, json, datetime

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live.db')

def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS positions(
        symbol TEXT PRIMARY KEY, qty INTEGER, entry_px REAL, entry_date TEXT,
        security_id TEXT, order_id TEXT, status TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS journal(
        ts TEXT, kind TEXT, symbol TEXT, side TEXT, qty INTEGER,
        price REAL, order_id TEXT, note TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS runs(
        ts TEXT PRIMARY KEY, payload TEXT)""")
    c.commit(); return c

def log(kind, symbol=None, side=None, qty=None, price=None, order_id=None, note=None):
    c = conn(); c.execute("INSERT INTO journal VALUES(?,?,?,?,?,?,?,?)",
        (datetime.datetime.now().isoformat()[:19], kind, symbol, side, qty, price, order_id, note))
    c.commit(); c.close()

def open_positions():
    c = conn(); r = [dict(x) for x in c.execute("SELECT * FROM positions WHERE status='OPEN'")]
    c.close(); return r

def add_position(symbol, qty, entry_px, entry_date, security_id, order_id):
    c = conn(); c.execute("INSERT OR REPLACE INTO positions VALUES(?,?,?,?,?,?,'OPEN')",
        (symbol, qty, entry_px, entry_date, security_id, order_id)); c.commit(); c.close()

def close_position(symbol):
    c = conn(); c.execute("UPDATE positions SET status='CLOSED' WHERE symbol=?", (symbol,))
    c.commit(); c.close()

def save_run(payload):
    c = conn(); c.execute("INSERT OR REPLACE INTO runs VALUES(?,?)",
        (datetime.datetime.now().isoformat()[:19], json.dumps(payload))); c.commit(); c.close()
