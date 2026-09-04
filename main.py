"""
main.py
=======
Le chef d'orchestre. Ce script tourne toutes les heures via GitHub
Actions. À chaque exécution :

  1. Recharge l'état sauvegardé (soldes, positions, historique de perf)
  2. Pour chaque stratégie x chaque crypto :
       - calcule le signal technique (Niveau 1, avec score de conviction)
       - le fait passer par le decision_engine (qui ajoute l'avis de
         l'IA si le Niveau 2 est activé -- sinon Niveau 1 seul)
       - applique les règles de risque et exécute si tout est au vert
  3. Vérifie les positions déjà ouvertes : SL/TP (géré par OKX via l'ordre
     OCO), invalidation du signal, ou stagnation temporelle
  4. Met à jour le suivi de performance (pause automatique si dégradation)
  5. Sauvegarde l'état + écrit dans le log
"""

import json
import os
import csv
import time
from datetime import datetime, timezone

from okx_client import OKXDemoClient
from risk_manager import RiskManager, calculate_atr
from strategies import ALL_STRATEGIES
from decision_engine import evaluate
import config


def load_state() -> dict:
    if os.path.exists(config.STATE_FILE):
        with open(config.STATE_FILE, "r") as f:
            return json.load(f)
    return {
        strat.name: {
            "starting_capital": config.STARTING_CAPITAL_PER_STRATEGY,
            "current_balance": config.STARTING_CAPITAL_PER_STRATEGY,
            "trading_halted": False,
            "strategy_paused": False,
            "pnl_history": [],
            "open_positions": {},
        }
        for strat in ALL_STRATEGIES
    }


def save_state(state: dict):
    with open(config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def log_event(strategy_name: str, symbol: str, action: str, price: float,
              quantity: float = 0.0, pnl: float = 0.0, note: str = ""):
    file_exists = os.path.exists(config.LOG_FILE)
    with open(config.LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "strategy", "symbol", "action", "price", "quantity", "pnl", "note"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(), strategy_name, symbol,
            action, price, quantity, pnl, note,
        ])


def build_risk_manager(strategy_name: str, saved: dict) -> RiskManager:
    rm = RiskManager(strategy_name, saved["starting_capital"])
    rm.current_balance = saved["current_balance"]
    rm.trading_halted = saved["trading_halted"]
    rm.strategy_paused = saved.get("strategy_paused", False)
    rm.pnl_history = saved.get("pnl_history", [])
    rm.open_positions = saved["open_positions"]
    return rm


def dump_risk_manager(rm: RiskManager) -> dict:
    return {
        "starting_capital": rm.starting_capital,
        "current_balance": rm.current_balance,
        "trading_halted": rm.trading_halted,
        "strategy_paused": rm.strategy_paused,
        "pnl_history": rm.pnl_history,
        "open_positions": {
            sym: {
                "entry_price": pos.entry_price if hasattr(pos, "entry_price") else pos["entry_price"],
                "quantity": pos.quantity if hasattr(pos, "quantity") else pos["quantity"],
                "stop_loss_price": pos.stop_loss_price if hasattr(pos, "stop_loss_price") else pos["stop_loss_price"],
                "take_profit_price": pos.take_profit_price if hasattr(pos, "take_profit_price") else pos["take_profit_price"],
                "opened_at": getattr(pos, "opened_at", None) if hasattr(pos, "opened_at")
                             else (pos.get("opened_at", time.time()) if isinstance(pos, dict) else time.time()),
            }
            for sym, pos in rm.open_positions.items()
        },
    }


def check_and_close_positions(client: OKXDemoClient, rm: RiskManager, strategy, strategy_name: str):
    """
    Vérifie chaque position ouverte pour trois raisons de sortie possibles :
    SL/TP (déjà géré par l'ordre OCO côté OKX, on vérifie juste si le prix
    les a dépassés pour garder notre état à jour), invalidation du signal,
    ou stagnation temporelle.
    """
    for symbol in list(rm.open_positions.keys()):
        pos = rm.open_positions[symbol]
        entry_price = pos["entry_price"] if isinstance(pos, dict) else pos.entry_price
        sl = pos["stop_loss_price"] if isinstance(pos, dict) else pos.stop_loss_price
        tp = pos["take_profit_price"] if isinstance(pos, dict) else pos.take_profit_price
        quantity = pos["quantity"] if isinstance(pos, dict) else pos.quantity

        current_price = client.get_current_price(symbol)

        if current_price <= sl or current_price >= tp:
            reason = "stop_loss" if current_price <= sl else "take_profit"
            pnl = rm.close_position(symbol, current_price)
            log_event(strategy_name, symbol, "close", current_price, quantity, pnl, reason)
            continue

        # Vérifie si le signal d'origine s'est inversé (sortie sur invalidation)
        candles = client.get_candles(symbol, config.CANDLE_INTERVAL, config.CANDLES_HISTORY_LENGTH)
        current_signal = strategy.get_signal(candles)

        exit_reason = rm.check_exit_conditions(symbol, current_price, current_signal.direction)
        if exit_reason:
            client.place_market_sell(symbol, quantity)
            pnl = rm.close_position(symbol, current_price)
            log_event(strategy_name, symbol, "close", current_price, quantity, pnl, exit_reason)


def run():
    client = OKXDemoClient()
    state = load_state()

    for strategy in ALL_STRATEGIES:
        rm = build_risk_manager(strategy.name, state[strategy.name])

        # 1. Vérifier les positions déjà ouvertes
        check_and_close_positions(client, rm, strategy, strategy.name)

        # 2. Chercher de nouveaux signaux
        for symbol in config.SYMBOLS:
            candles = client.get_candles(symbol, config.CANDLE_INTERVAL, config.CANDLES_HISTORY_LENGTH)
            signal = strategy.get_signal(candles)

            if not signal.is_actionable:
                continue

            current_price = client.get_current_price(symbol)

            # Passe par le moteur de décision : ajoute l'avis IA si activé,
            # sinon garde le score du Niveau 1 tel quel.
            signal = evaluate(symbol, strategy.name, signal, current_price)

            if not signal.is_actionable:
                log_event(strategy.name, symbol, "signal_rejected", current_price,
                          note="; ".join(signal.reasons))
                continue

            if signal.direction == "buy":
                allowed, reason = rm.can_open_position(symbol)
                if not allowed:
                    log_event(strategy.name, symbol, "skip_buy", current_price, note=reason)
                    continue

                atr = calculate_atr(candles["high"], candles["low"], candles["close"])
                sizing = rm.calculate_position(symbol, current_price, atr, entry_reasons=signal.reasons)
                if sizing is None:
                    continue

                client.place_market_buy(symbol, sizing.quantity)
                client.place_stop_loss_take_profit(
                    symbol, sizing.quantity, sizing.stop_loss_price, sizing.take_profit_price
                )
                rm.register_open_position(sizing)
                note = f"score={signal.conviction_score:.0f} | SL={sizing.stop_loss_price} TP={sizing.take_profit_price}"
                if signal.ai_reviewed:
                    note += f" | IA: {signal.ai_notes}"
                log_event(strategy.name, symbol, "buy", current_price, sizing.quantity, note=note)

            elif signal.direction == "sell" and symbol in rm.open_positions:
                pos = rm.open_positions[symbol]
                quantity = pos.quantity if hasattr(pos, "quantity") else pos["quantity"]
                client.place_market_sell(symbol, quantity)
                pnl = rm.close_position(symbol, current_price)
                log_event(strategy.name, symbol, "sell_signal_close", current_price, quantity, pnl)

        # 3. Sauvegarder l'état mis à jour de cette stratégie
        state[strategy.name] = dump_risk_manager(rm)

    save_state(state)
    print("Exécution terminée :", datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    run()
