"""
generate_report.py
===================
Génère un résumé clair et lisible de l'état du bot : solde de chaque
stratégie, évolution par rapport au capital de départ, nombre de trades,
et les derniers trades pris. Affiché directement dans les logs GitHub
Actions -- pas besoin d'ouvrir de fichier technique pour suivre le bot.
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
    print("=" * 60)

    for strategy_name, data in state.items():
        starting = data["starting_capital"]
        current = data["current_balance"]
        evolution_pct = ((current - starting) / starting) * 100
        evolution_usd = current - starting
        halted = data["trading_halted"]
        open_positions = data["open_positions"]

        print(f"\n--- Stratégie : {strategy_name} ---")
        print(f"  Capital de départ : {starting:.2f}$")
        print(f"  Solde actuel      : {current:.2f}$")
        sign = "+" if evolution_usd >= 0 else ""
        print(f"  Évolution         : {sign}{evolution_usd:.2f}$ ({sign}{evolution_pct:.2f}%)")
        print(f"  Statut            : {'ARRÊTÉ (circuit breaker)' if halted else 'Actif'}")
        print(f"  Positions ouvertes: {len(open_positions)}")
        for symbol, pos in open_positions.items():
            entry = pos["entry_price"] if isinstance(pos, dict) else pos.entry_price
            print(f"    - {symbol} @ {entry}")

        strat_trades = [t for t in trades if t["strategy"] == strategy_name]
        print(f"  Nombre total de trades enregistrés : {len(strat_trades)}")

    print("\n" + "=" * 60)
    print(f"DERNIERS TRADES (les {min(10, len(trades))} plus récents)")
    print("=" * 60)
    for t in trades[-10:]:
        print(f"  [{t['timestamp']}] {t['strategy']} | {t['symbol']} | {t['action']} "
              f"@ {t['price']} | qty={t['quantity']} | pnl={t['pnl']} | {t['note']}")

    if not trades:
        print("  Aucun trade pris pour l'instant.")

    print("=" * 60)


if __name__ == "__main__":
    print_report()
