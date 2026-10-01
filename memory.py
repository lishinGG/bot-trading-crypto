"""
memory.py
=========
Système de mémoire du bot : donne à l'IA (Niveau 2 UNIQUEMENT -- jamais
le Niveau 1) des statistiques vérifiées sur la performance RÉELLE passée
d'une combinaison stratégie+crypto précise.

RÈGLES ANTI-HALLUCINATION STRICTES :
  - Uniquement des statistiques calculées à partir de VRAIS trades fermés
    (lus directement dans trading_log.csv, jamais inventés)
  - En dessous du seuil minimum d'échantillon (30 trades), AUCUNE
    statistique n'est fournie -- une chaîne vide, pas une fausse leçon
    tirée d'un échantillon trop petit pour être fiable
  - Le résumé transmis à l'IA est composé de CHIFFRES bruts, jamais
    d'une narration ou d'une interprétation -- pour éviter que l'IA ne
    "raconte une histoire" à partir de peu de données
"""

import csv
import os
import config

MIN_TRADES_FOR_MEMORY = 30


def _load_closed_trades(log_key: str, symbol: str) -> list:
    """Lit trading_log.csv et retourne les PnL des fermetures réelles
    pour cette combinaison stratégie+crypto exacte (log_key doit
    correspondre EXACTEMENT à la colonne 'strategy' du CSV, ex:
    'trend_following' pour le spot réel, 'trend_following__futures'
    pour la piste Futures -- jamais le compte fantôme, qui reste une
    référence Niveau 1 pure, non affectée par la mémoire)."""
    if not os.path.exists(config.LOG_FILE):
        return []

    pnls = []
    with open(config.LOG_FILE, "r") as f:
        for row in csv.DictReader(f):
            if row.get("strategy") != log_key or row.get("symbol") != symbol:
                continue
            if row.get("action") not in ("close", "sell_signal_close"):
                continue
            try:
                pnls.append(float(row["pnl"]))
            except (ValueError, KeyError, TypeError):
                continue
    return pnls


def get_memory_context(log_key: str, symbol: str) -> str:
    """
    Retourne un résumé texte des statistiques réelles passées, ou une
    chaîne VIDE si l'échantillon est trop petit (< MIN_TRADES_FOR_MEMORY).
    Une chaîne vide signifie "pas de mémoire disponible" -- le
    comportement du bot reste alors identique à avant cette fonctionnalité.
    """
    pnls = _load_closed_trades(log_key, symbol)
    count = len(pnls)

    if count < MIN_TRADES_FOR_MEMORY:
        return ""

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = (len(wins) / count) * 100
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0

    return (
        f"Historique RÉEL vérifié sur {symbol} pour cette stratégie ({count} trades fermés) :\n"
        f"  - Taux de réussite historique : {win_rate:.0f}%\n"
        f"  - Gain moyen sur trade gagnant : {avg_win:+.2f}$\n"
        f"  - Perte moyenne sur trade perdant : {avg_loss:+.2f}$\n"
        f"Ce sont des chiffres réels et vérifiés (pas une estimation) -- utilise-les comme un "
        f"fait, sans en déduire de motif ou de cause au-delà de ce qui est donné ici."
    )
