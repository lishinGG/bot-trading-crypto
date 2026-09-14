"""
ai_usage_tracker.py
====================
Suit le nombre d'appels faits à l'IA chaque jour, pour surveiller
l'approche de la limite gratuite d'OpenRouter (50 requêtes/jour).
Stocké dans un petit fichier séparé (pas dans state.json) pour rester
indépendant du reste de l'état du bot.
"""

import json
import os
from datetime import datetime, timezone

USAGE_FILE = "ai_usage.json"
DAILY_LIMIT_WARNING_THRESHOLD = 40   # avertit dès qu'on approche la limite de 50/jour


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_usage() -> dict:
    if os.path.exists(USAGE_FILE):
        with open(USAGE_FILE, "r") as f:
            data = json.load(f)
        if data.get("date") == _today_str():
            return data
    return {"date": _today_str(), "call_count": 0}


def record_ai_call():
    """À appeler à chaque tentative d'appel à l'IA (succès ou échec compris,
    puisque même un appel échoué consomme potentiellement le quota)."""
    usage = load_usage()
    usage["call_count"] += 1
    with open(USAGE_FILE, "w") as f:
        json.dump(usage, f, indent=2)
    return usage["call_count"]


def get_today_usage() -> dict:
    """Retourne l'usage du jour, avec une alerte si on approche la limite."""
    usage = load_usage()
    count = usage["call_count"]
    return {
        "date": usage["date"],
        "call_count": count,
        "approaching_limit": count >= DAILY_LIMIT_WARNING_THRESHOLD,
    }
