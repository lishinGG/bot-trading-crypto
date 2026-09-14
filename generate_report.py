"""
generate_report.py
===================
Génère un résumé clair et lisible de l'état du bot : solde de chaque
stratégie, évolution, statut (actif/pause/arrêté), les derniers trades,
et une comparaison avec/sans IA (mode "compte fantôme").
"""

import json
import csv
import os
import config
import ai_usage_tracker
from strategies import ALL_STRATEGIES


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


def print_strategy_block(name: str, data: dict, trades: list, label: str = ""):
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

    title = f"--- Stratégie : {name}{label} ---"
    print(f"\n{title}")
    print(f"  Capital de départ : {starting:.2f}$")
    print(f"  Solde actuel      : {current:.2f}$")
    sign = "+" if evolution_usd >= 0 else ""
    print(f"  Évolution         : {sign}{evolution_usd:.2f}$ ({sign}{evolution_pct:.2f}%)")
    print(f"  Statut            : {status}")
    print(f"  Positions ouvertes: {sum(len(v) for v in open_positions.values())}")
    for symbol, positions in open_positions.items():
        for pos in positions:
            entry = pos["entry_price"] if isinstance(pos, dict) else pos.entry_price
            print(f"    - {symbol} @ {entry}")

    if pnl_history:
        wins = sum(1 for p in pnl_history if p > 0)
        print(f"  Taux de réussite (derniers {len(pnl_history)} trades) : {wins}/{len(pnl_history)}")

    strat_trades = [t for t in trades if t["strategy"] == name]
    print(f"  Nombre total d'événements enregistrés : {len(strat_trades)}")

    return evolution_usd, evolution_pct


def print_report():
    state = load_state()
    trades = load_trades()

    print("=" * 60)
    print("RAPPORT DE SUIVI DU BOT DE TRADING")
    if config.USE_AI_LAYER:
        print(f"Couche IA : ACTIVE ({config.AI_PROVIDER})")
        if config.AI_PROVIDER == "openrouter":
            usage = ai_usage_tracker.get_today_usage()
            warning = "  ATTENTION, proche de la limite gratuite (50/jour)" if usage["approaching_limit"] else ""
            print(f"Appels IA aujourd'hui ({usage['date']}) : {usage['call_count']}/50{warning}")
    else:
        print("Couche IA : désactivée (Niveau 1 uniquement)")
    print("=" * 60)

    comparison_rows = []

    for strat in ALL_STRATEGIES:
        real_data = state.get(strat.name)
        shadow_data = state.get(f"{strat.name}__shadow")
        if real_data is None:
            continue

        real_evo_usd, real_evo_pct = print_strategy_block(strat.name, real_data, trades, label=" (avec IA)")

        if shadow_data is not None:
            shadow_evo_usd, shadow_evo_pct = print_strategy_block(
                strat.name, shadow_data, trades, label=" (SANS IA -- compte fantôme)"
            )
            comparison_rows.append((strat.name, real_evo_usd, real_evo_pct, shadow_evo_usd, shadow_evo_pct))

    if comparison_rows:
        print("\n" + "=" * 60)
        print("COMPARAISON AVEC IA vs SANS IA (mode fantôme)")
        print("=" * 60)
        for name, real_usd, real_pct, shadow_usd, shadow_pct in comparison_rows:
            diff_usd = real_usd - shadow_usd
            verdict = "L'IA fait MIEUX" if diff_usd > 0 else ("L'IA fait MOINS BIEN" if diff_usd < 0 else "Égalité")
            print(f"  {name} :")
            print(f"    Avec IA  : {real_usd:+.2f}$ ({real_pct:+.2f}%)")
            print(f"    Sans IA  : {shadow_usd:+.2f}$ ({shadow_pct:+.2f}%)")
            print(f"    Écart    : {diff_usd:+.2f}$ -> {verdict}")

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
