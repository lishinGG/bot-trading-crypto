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

# Intervalle des bougies utilisées pour l'analyse (correspond à la
# fréquence horaire choisie)
CANDLE_INTERVAL = "1H"
CANDLES_HISTORY_LENGTH = 100  # nombre de bougies récupérées à chaque analyse

# Fichier où l'état du bot (soldes, positions ouvertes, historique) est
# sauvegardé entre deux exécutions -- indispensable car GitHub Actions
# ne garde rien en mémoire d'une exécution à l'autre.
STATE_FILE = "state.json"
LOG_FILE = "trading_log.csv"
