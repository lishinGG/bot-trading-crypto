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
CANDLES_HISTORY_LENGTH = 250  # 250 pour laisser de la marge à la tendance de fond (200 périodes)

STATE_FILE = "state.json"
LOG_FILE = "trading_log.csv"

# ----------------------------------------------------------------------
# COUCHE IA (Niveau 2) -- désactivée par défaut, prête à l'emploi
# ----------------------------------------------------------------------
# Pour activer : mets USE_AI_LAYER = True et choisis AI_PROVIDER.
#   "openrouter" -> gratuit, nécessite OPENROUTER_API_KEY dans les secrets
#   "anthropic"  -> payant (Claude), nécessite ANTHROPIC_API_KEY
USE_AI_LAYER = False
AI_PROVIDER = "openrouter"

OPENROUTER_MODEL = "inclusionai/ling-3.0-flash-fin:free"
ANTHROPIC_MODEL = "claude-sonnet-5"
