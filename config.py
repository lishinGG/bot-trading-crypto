SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XLM-USDT", "DOGE-USDT", "LINK-USDT"]
STARTING_CAPITAL_PER_STRATEGY = 500.0
CANDLE_INTERVAL = "1H"
CANDLES_HISTORY_LENGTH = 250
STATE_FILE = "state.json"
LOG_FILE = "trading_log.csv"
USE_AI_LAYER = True
AI_PROVIDER = "openrouter"

# Modèles gratuits OpenRouter, essayés DANS L'ORDRE : si l'un est retiré (erreur 404) ou répond mal,
# le suivant est utilisé automatiquement. "openrouter/free" est le routeur d'OpenRouter : il choisit
# lui-même un modèle gratuit disponible (dernier recours, qualité variable).
OPENROUTER_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "qwen/qwen3.8-27b:free",
    "openrouter/free",
]
OPENROUTER_MODEL = OPENROUTER_MODELS[0]   # conservé pour compatibilité
ANTHROPIC_MODEL = "claude-sonnet-5"

USE_FUTURES = True
FUTURES_SYMBOLS = {s: f"{s}-SWAP" for s in SYMBOLS}
FUTURES_LEVERAGE = 5
FUTURES_MARGIN_MODE = "isolated"
FUTURES_STARTING_CAPITAL_PER_STRATEGY = 500.0
FUTURES_AI_REVIEW = True
FUTURES_ALLOW_SHORT = True   # positions courtes autorisées en Futures uniquement (jamais en spot)
