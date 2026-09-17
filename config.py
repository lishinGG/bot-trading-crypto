"""
config.py
=========
Tous les réglages "métier" du bot regroupés au même endroit.
"""

SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XLM-USDT"]

STARTING_CAPITAL_PER_STRATEGY = 500.0

CANDLE_INTERVAL = "1H"
CANDLES_HISTORY_LENGTH = 250

STATE_FILE = "state.json"
LOG_FILE = "trading_log.csv"

USE_AI_LAYER = True
AI_PROVIDER = "openrouter"

OPENROUTER_MODEL = "inclusionai/ling-3.0-flash-fin:free"
ANTHROPIC_MODEL = "claude-sonnet-5"

# ----------------------------------------------------------------------
# NOTIFICATIONS TELEGRAM
# ----------------------------------------------------------------------
USE_NOTIFICATIONS = True
