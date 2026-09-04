"""
performance_tracker.py
=======================
Garde un historique glissant des derniers trades de chaque stratégie et
la met en "pause" temporaire si son taux de réussite se dégrade
nettement -- pour refléter ce que montre la recherche : une stratégie
peut fonctionner pendant longtemps puis arrêter de marcher quand le
marché change de régime.

"Pause" ne veut pas dire "désactivée pour toujours" -- la stratégie
continue d'être évaluée en arrière-plan (ses signaux sont calculés mais
pas exécutés) et reprend automatiquement si sa performance récente
s'améliore.
"""

ROLLING_WINDOW = 20          # nombre de trades récents pris en compte
MIN_TRADES_TO_JUDGE = 8      # pas de jugement avant d'avoir assez de données
PAUSE_WIN_RATE_THRESHOLD = 0.30   # en dessous de 30% de réussite -> pause


def compute_rolling_stats(pnl_history: list[float]) -> dict:
    """
    pnl_history : liste des profits/pertes ($) des derniers trades fermés,
    dans l'ordre chronologique (le plus récent en dernier).
    """
    recent = pnl_history[-ROLLING_WINDOW:]
    if len(recent) < MIN_TRADES_TO_JUDGE:
        return {"win_rate": None, "should_pause": False, "sample_size": len(recent)}

    wins = sum(1 for pnl in recent if pnl > 0)
    win_rate = wins / len(recent)
    should_pause = win_rate < PAUSE_WIN_RATE_THRESHOLD

    return {"win_rate": round(win_rate, 2), "should_pause": should_pause, "sample_size": len(recent)}


def update_pnl_history(pnl_history: list[float], new_pnl: float) -> list[float]:
    """Ajoute un nouveau résultat, garde uniquement la fenêtre glissante."""
    updated = pnl_history + [new_pnl]
    return updated[-ROLLING_WINDOW:]
