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
# FUTURES (contrats perpétuels à levier) -- piste séparée du spot
# ----------------------------------------------------------------------
# Utilise les VRAIS contrats perpétuels démo d'OKX (BTC-USDT-SWAP, etc.)
# -- pas un simulateur maison. OKX gère nativement marge/liquidation/funding.
USE_FUTURES = True
FUTURES_SYMBOLS = {s: f"{s}-SWAP" for s in SYMBOLS}   # "BTC-USDT" -> "BTC-USDT-SWAP"
FUTURES_LEVERAGE = 5
FUTURES_MARGIN_MODE = "isolated"   # isolated = seul le capital de CETTE position est à risque
FUTURES_STARTING_CAPITAL_PER_STRATEGY = 500.0
FUTURES_AI_REVIEW = True   # l'IA évalue aussi les trades Futures (appel séparé du spot)
