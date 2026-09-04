"""
generate_report.py
===================
Génère un résumé clair et lisible de l'état du bot : solde de chaque
stratégie, évolution, statut (actif/pause/arrêté), et les derniers trades.
Affiché directement dans les logs GitHub Actions.
"""

import json
import csv
import os
import config


def load_state():
    if not os.path.exists(config.STATE_FILE):
        print("Aucun état trouvé pour l'instant -- le bot n'a pas encore tourné.")
        return {}
    with open(config.STATE_FILE, "r") as f:
        return json.load(f)


def load_trades():
    if not os.path.exists(config.LOG_FILE):
        return []
    with open(config.LOG_FILE, "r") as f:
        return list(csv.DictReader(f))


def print_report():
    state = load_state()
    trades = load_trades()

    print("=" * 60)
    print("RAPPORT DE SUIVI DU BOT DE TRADING")
    if config.USE_AI_LAYER:
        print(f"Couche IA : ACTIVE ({config.AI_PROVIDER})")
    else:
        print("Couche IA : désactivée (Niveau 1 uniquement)")
    print("=" * 60)

    for strategy_name, data in state.items():
        starting = data["starting_capital"]
        current = data["current_balance"]
        evolution_pct = ((current - starting) / starting) * 100
        evolution_usd = current - starting
        open_positions = data["open_positions"]
        pnl_history = data.get("pnl_history", [])

        if data.get("trading_halted"):
            status = "ARRÊTÉ (circuit breaker -20%)"
        elif data.get("strategy_paused"):
            status = "EN PAUSE (performance récente dégradée)"
        else:
            status = "Actif"

        print(f"\n--- Stratégie : {strategy_name} ---")
        print(f"  Capital de départ : {starting:.2f}$")
        print(f"  Solde actuel      : {current:.2f}$")
        sign = "+" if evolution_usd >= 0 else ""
        print(f"  Évolution         : {sign}{evolution_usd:.2f}$ ({sign}{evolution_pct:.2f}%)")
        print(f"  Statut            : {status}")
        print(f"  Positions ouvertes: {len(open_positions)}")
        for symbol, pos in open_positions.items():
            entry = pos["entry_price"] if isinstance(pos, dict) else pos.entry_price
            print(f"    - {symbol} @ {entry}")

        if pnl_history:
            wins = sum(1 for p in pnl_history if p > 0)
            print(f"  Taux de réussite (derniers {len(pnl_history)} trades) : {wins}/{len(pnl_history)}")

        strat_trades = [t for t in trades if t["strategy"] == strategy_name]
        print(f"  Nombre total d'événements enregistrés : {len(strat_trades)}")

    print("\n" + "=" * 60)
    print(f"DERNIERS ÉVÉNEMENTS (les {min(15, len(trades))} plus récents)")
    print("=" * 60)
    for t in trades[-15:]:
        print(f"  [{t['timestamp']}] {t['strategy']} | {t['symbol']} | {t['action']} "
              f"@ {t['price']} | qty={t['quantity']} | pnl={t['pnl']} | {t['note']}")

    if not trades:
        print("  Aucun événement pour l'instant.")

    print("=" * 60)


if __name__ == "__main__":
    print_report()
