"""
generate_report.py
===================
Rapport de suivi : pour chaque stratégie, affiche le compte réel (spot,
avec IA), le compte fantôme (spot, sans IA), et le compte Futures (levier,
avec IA) si activé -- avec une comparaison chiffrée entre les trois.
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


def print_strategy_block(display_name: str, log_key: str, data: dict, trades: list, label: str = ""):
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

    print(f"\n--- Stratégie : {display_name}{label} ---")
    print(f"  Capital de départ : {starting:.2f}$")
    print(f"  Solde actuel      : {current:.2f}$")
    sign = "+" if evolution_usd >= 0 else ""
    print(f"  Évolution         : {sign}{evolution_usd:.2f}$ ({sign}{evolution_pct:.2f}%)")
    print(f"  Statut            : {status}")
    print(f"  Positions ouvertes: {sum(len(v) for v in open_positions.values())}")
    for symbol, positions in open_positions.items():
        for pos in positions:
            entry = pos["entry_price"] if isinstance(pos, dict) else pos.entry_price
            leverage = pos.get("leverage", 1.0) if isinstance(pos, dict) else getattr(pos, "leverage", 1.0)
            lev_note = f" (levier {leverage:.0f}x)" if leverage and leverage > 1 else ""
            print(f"    - {symbol} @ {entry}{lev_note}")

    if pnl_history:
        wins = sum(1 for p in pnl_history if p > 0)
        print(f"  Taux de réussite (derniers {len(pnl_history)} trades) : {wins}/{len(pnl_history)}")

    strat_trades = [t for t in trades if t["strategy"] == log_key]
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
    if config.USE_FUTURES:
        print(f"Futures : ACTIF (levier {config.FUTURES_LEVERAGE}x, mode {config.FUTURES_MARGIN_MODE})")
    print("=" * 60)

    comparison_rows = []

    for strat in ALL_STRATEGIES:
        real_data = state.get(strat.name)
        shadow_data = state.get(f"{strat.name}__shadow")
        futures_data = state.get(f"{strat.name}__futures") if config.USE_FUTURES else None
        if real_data is None:
            continue

        real_evo_usd, real_evo_pct = print_strategy_block(strat.name, strat.name, real_data, trades, label=" (spot, avec IA)")

        shadow_evo = None
        if shadow_data is not None:
            shadow_evo = print_strategy_block(strat.name, f"{strat.name}__shadow", shadow_data, trades,
                                               label=" (spot, SANS IA -- fantôme)")

        futures_evo = None
        if futures_data is not None:
            futures_evo = print_strategy_block(strat.name, f"{strat.name}__futures", futures_data, trades,
                                                label=f" (Futures {config.FUTURES_LEVERAGE}x, avec IA)")

        comparison_rows.append((strat.name, (real_evo_usd, real_evo_pct), shadow_evo, futures_evo))

    if comparison_rows:
        print("\n" + "=" * 60)
        print("COMPARAISON SPOT AVEC IA vs SANS IA vs FUTURES")
        print("=" * 60)
        for name, real_evo, shadow_evo, futures_evo in comparison_rows:
            real_usd, real_pct = real_evo
            print(f"  {name} :")
            print(f"    Spot avec IA    : {real_usd:+.2f}$ ({real_pct:+.2f}%)")
            if shadow_evo:
                shadow_usd, shadow_pct = shadow_evo
                diff = real_usd - shadow_usd
                verdict = "IA fait MIEUX" if diff > 0 else ("IA fait MOINS BIEN" if diff < 0 else "Égalité")
                print(f"    Spot sans IA    : {shadow_usd:+.2f}$ ({shadow_pct:+.2f}%)  -> {verdict}")
            if futures_evo:
                futures_usd, futures_pct = futures_evo
                diff_f = futures_usd - real_usd
                verdict_f = "Futures fait MIEUX que spot" if diff_f > 0 else ("Futures fait MOINS BIEN que spot" if diff_f < 0 else "Égalité")
                print(f"    Futures {config.FUTURES_LEVERAGE}x avec IA : {futures_usd:+.2f}$ ({futures_pct:+.2f}%)  -> {verdict_f}")

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
