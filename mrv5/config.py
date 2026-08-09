"""All tunable parameters. Change here, re-run backtest.py, compare."""

# ---------- universe ----------
UNIVERSE_SIZE     = 250
TURNOVER_LOOKBACK = 750
MIN_HISTORY       = 260

# ---------- signal (from Connors; NOT tuned on this data) ----------
RSI_LEN       = 2
BUY_BELOW     = 5.0
TREND_SMA     = 200
EXIT_ABOVE    = 65.0
EXIT_SMA      = 5
MAX_HOLD_DAYS = 10
USE_STOP_LOSS = False    # validated: stops make this WORSE. Leave False.
STOP_PCT      = 0.08

# ---------- portfolio ----------
CAPITAL        = 1_000_000
SLOTS          = 10
COMPOUND       = True    # True | False | 'half'
COST_ROUNDTRIP = 0.0025  # stress to 0.0040 and 0.0060

# ---------- risk layer ----------
HEDGE_ON          = True
HEDGE_INDEX       = 'nifty 500'
HEDGE_INDEX_SMA   = 200
HEDGE_BREADTH_MIN = 0.40
HEDGE_RATIO       = 1.0
HEDGE_TOGGLE_COST = 0.0002

EQ_BRAKE_ON  = True
EQ_BRAKE_PCT = 0.10
EQ_BRAKE_WIN = 60

# ---------- window ----------
START = '2012-06-01'
END   = None
# In-sample / out-of-sample boundary used by the dashboard's
# "Backtest" vs "Forward test" tabs.
SPLIT_DATE = '2020-01-01'
