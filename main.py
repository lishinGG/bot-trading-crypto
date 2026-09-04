"""
main.py
=======
Le chef d'orchestre. C'est ce script que GitHub Actions exécute toutes
les heures. À chaque exécution, il :

  1. Recharge l'état sauvegardé (soldes, positions ouvertes) depuis state.json
  2. Pour chaque stratégie x chaque crypto : récupère les prix, calcule le
     signal, applique les règles de risque, exécute le trade si besoin
  3. Vérifie les positions déjà ouvertes (SL/TP touché ?)
  4. Sauvegarde le nouvel état + écrit dans le log
  5. S'arrête (jusqu'à la prochaine exécution planifiée)

NOTE IMPORTANTE : ce script est structuré pour tourner en mode "démo
papier" (paper trading géré par CE script), où le bot lui-même simule
le compte plutôt que de dépendre uniquement du solde renvoyé par OKX.
Ça permet d'avoir 3 comptes virtuels séparés (un par stratégie) même si
OKX lui-même n'a qu'un seul compte démo. Les ordres réels sont quand
même envoyés à OKX en mode démo pour que les prix d'exécution soient
réalistes (slippage, spread...).
"""

import json
import os
import csv
from datetime import datetime, timezone

from okx_client import OKXDemoClient
from risk_manager import RiskManager, calculate_atr
from strategies import ALL_STRATEGIES
import config


def load_state() -> dict:
    """Recharge l'état sauvegardé, ou crée un état neuf si premier lancement."""
    if os.path.exists(config.STATE_FILE):
        with open(config.STATE_FILE, "r") as f:
            return json.load(f)

    # Premier lancement : un état neuf, un "sous-compte" virtuel par stratégie
    return {
        strat.name: {
            "starting_capital": config.STARTING_CAPITAL_PER_STRATEGY,
            "current_balance": config.STARTING_CAPITAL_PER_STRATEGY,
            "trading_halted": False,
            "open_positions": {},   # {symbol: {entry_price, quantity, stop_loss, take_profit}}
        }
        for strat in ALL_STRATEGIES
    }


def save_state(state: dict):
    with open(config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def log_event(strategy_name: str, symbol: str, action: str, price: float,
              quantity: float = 0.0, pnl: float = 0.0, note: str = ""):
    """Ajoute une ligne au fichier de log CSV, pour analyser après coup."""
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
    """Reconstruit un RiskManager à partir de l'état sauvegardé."""
    rm = RiskManager(strategy_name, saved["starting_capital"])
    rm.current_balance = saved["current_balance"]
    rm.trading_halted = saved["trading_halted"]
    rm.open_positions = saved["open_positions"]
    return rm


def dump_risk_manager(rm: RiskManager) -> dict:
    return {
        "starting_capital": rm.starting_capital,
        "current_balance": rm.current_balance,
        "trading_halted": rm.trading_halted,
        "open_positions": {
            sym: {
                "entry_price": pos.entry_price if hasattr(pos, "entry_price") else pos["entry_price"],
                "quantity": pos.quantity if hasattr(pos, "quantity") else pos["quantity"],
                "stop_loss_price": pos.stop_loss_price if hasattr(pos, "stop_loss_price") else pos["stop_loss_price"],
                "take_profit_price": pos.take_profit_price if hasattr(pos, "take_profit_price") else pos["take_profit_price"],
            }
            for sym, pos in rm.open_positions.items()
        },
    }


def check_and_close_positions(client: OKXDemoClient, rm: RiskManager, strategy_name: str):
    """Vérifie si une position ouverte a touché son SL ou son TP."""
    for symbol in list(rm.open_positions.keys()):
        pos = rm.open_positions[symbol]
        entry_price = pos["entry_price"] if isinstance(pos, dict) else pos.entry_price
        sl = pos["stop_loss_price"] if isinstance(pos, dict) else pos.stop_loss_price
        tp = pos["take_profit_price"] if isinstance(pos, dict) else pos.take_profit_price

        current_price = client.get_current_price(symbol)

        if current_price <= sl or current_price >= tp:
            reason = "stop_loss" if current_price <= sl else "take_profit"
            quantity = pos["quantity"] if isinstance(pos, dict) else pos.quantity
            pnl = rm.close_position(symbol, current_price)
            log_event(strategy_name, symbol, "close", current_price, quantity, pnl, reason)


def run():
    client = OKXDemoClient()
    state = load_state()

    for strategy in ALL_STRATEGIES:
        rm = build_risk_manager(strategy.name, state[strategy.name])

        # 1. D'abord, vérifier si des positions déjà ouvertes doivent être fermées
        check_and_close_positions(client, rm, strategy.name)

        # 2. Ensuite, chercher de nouveaux signaux sur chaque crypto
        for symbol in config.SYMBOLS:
            candles = client.get_candles(symbol, config.CANDLE_INTERVAL, config.CANDLES_HISTORY_LENGTH)
            signal = strategy.get_signal(candles)

            if signal == "buy":
                allowed, reason = rm.can_open_position(symbol)
                if not allowed:
                    log_event(strategy.name, symbol, "skip_buy", candles["close"][-1], note=reason)
                    continue

                entry_price = client.get_current_price(symbol)
                atr = calculate_atr(candles["high"], candles["low"], candles["close"])
                sizing = rm.calculate_position(symbol, entry_price, atr)

                if sizing is None:
                    continue

                # Envoie l'ordre réel en mode démo à OKX
                client.place_market_buy(symbol, sizing.quantity)
                client.place_stop_loss_take_profit(
                    symbol, sizing.quantity, sizing.stop_loss_price, sizing.take_profit_price
                )
                rm.register_open_position(sizing)
                log_event(strategy.name, symbol, "buy", entry_price, sizing.quantity,
                          note=f"SL={sizing.stop_loss_price} TP={sizing.take_profit_price}")

            elif signal == "sell" and symbol in rm.open_positions:
                current_price = client.get_current_price(symbol)
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
