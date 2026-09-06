"""
config.py
=========
Tous les réglages "métier" du bot regroupés au même endroit.
Change ces valeurs ici plutôt que dans le code des autres fichiers.
"""

# Les 5 cryptos suivies (format attendu par l'API OKX : "XXX-USDT")
SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XLM-USDT"]

# Budget de départ (virtuel) pour CHAQUE stratégie
STARTING_CAPITAL_PER_STRATEGY = 500.0

# Intervalle des bougies utilisées pour l'analyse
CANDLE_INTERVAL = "1H"
CANDLES_HISTORY_LENGTH = 250

STATE_FILE = "state.json"
LOG_FILE = "trading_log.csv"

# ----------------------------------------------------------------------
# COUCHE IA (Niveau 2) -- prete a l'emploi
# ----------------------------------------------------------------------
USE_AI_LAYER = True
AI_PROVIDER = "openrouter"

OPENROUTER_MODEL = "inclusionai/ling-3.0-flash-fin:free"
ANTHROPIC_MODEL = "claude-sonnet-5"
