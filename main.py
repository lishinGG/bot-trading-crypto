"""
main.py
=======
Le chef d'orchestre. Ce script tourne toutes les heures via GitHub
Actions. À chaque exécution :

  1. Recharge l'état sauvegardé (soldes, positions -- désormais des
     LISTES par crypto -- historique de perf, comptes fantômes)
  2. Pour chaque stratégie x chaque crypto :
       - calcule le signal technique (Niveau 1, avec score de conviction)
       - alimente le compte fantôme (simulation Niveau 1 pur, sans IA)
       - le fait passer par le decision_engine (IA optionnelle)
       - applique les règles de risque et exécute si tout est au vert
  3. Vérifie CHAQUE position ouverte : SL/TP, trailing stop, invalidation,
     stagnation
  4. Met à jour le suivi de performance
  5. Sauvegarde l'état + écrit dans le log
"""

import json
import os
import csv
import time
import uuid
from datetime import datetime, timezone

from okx_client import OKXDemoClient
from risk_manager import RiskManager, calculate_atr
from strategies import ALL_STRATEGIES
from decision_engine import evaluate
from indicators import moving_average
import config


def build_higher_timeframe_context(client: OKXDemoClient, symbol: str) -> str:
    """Construit un résumé texte de la tendance sur 4h et 1 jour."""
    try:
        summary_lines = []
        for label, bar in [("4h", "4H"), ("1 jour", "1D")]:
            candles = client.get_candles(symbol, bar, limit=60)
            closes = candles["close"]
            if len(closes) < 21:
                continue
            long_ma = moving_average(closes, min(50, len(closes)))
            current = closes[-1]
            position = "au-dessus" if current > long_ma else "en dessous"
            change_pct = ((closes[-1] - closes[-20]) / closes[-20]) * 100
            summary_lines.append(
                f"  - Échéance {label} : prix {position} de sa moyenne longue "
                f"(tendance {'haussière' if current > long_ma else 'baissière'} de fond), "
                f"variation sur les 20 dernières bougies : {change_pct:+.1f}%"
            )
        return "\n".join(summary_lines)
    except Exception as e:
        print(f"[main] Contexte multi-timeframe indisponible pour {symbol} : {e}")
        return ""


def _new_strategy_state() -> dict:
    return {
        "starting_capital": config.STARTING_CAPITAL_PER_STRATEGY,
        "current_balance": config.STARTING_CAPITAL_PER_STRATEGY,
        "trading_halted": False,
        "strategy_paused": False,
        "pnl_history": [],
        "open_positions": {},
    }


def load_state() -> dict:
    if os.path.exists(config.STATE_FILE):
        with open(config.STATE_FILE, "r") as f:
            state = json.load(f)
        for strat in ALL_STRATEGIES:
            state.setdefault(f"{strat.name}__shadow", _new_strategy_state())
        return state

    state = {}
    for strat in ALL_STRATEGIES:
        state[strat.name] = _new_strategy_state()
        state[f"{strat.name}__shadow"] = _new_strategy_state()
    return state


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


def _position_to_dict(pos) -> dict:
    if isinstance(pos, dict):
        return pos
    return {
        "position_id": pos.position_id,
        "entry_price": pos.entry_price,
        "quantity": pos.quantity,
        "stop_loss_price": pos.stop_loss_price,
        "take_profit_price": pos.take_profit_price,
        "opened_at": pos.opened_at,
        "algo_id": pos.algo_id,
        "highest_price_seen": pos.highest_price_seen,
    }


def build_risk_manager(strategy_name: str, saved: dict) -> RiskManager:
    rm = RiskManager(strategy_name, saved["starting_capital"])
    rm.current_balance = saved["current_balance"]
    rm.trading_halted = saved["trading_halted"]
    rm.strategy_paused = saved.get("strategy_paused", False)
    rm.pnl_history = saved.get("pnl_history", [])

    raw_positions = saved.get("open_positions", {})
    rm.open_positions = {}
    for symbol, value in raw_positions.items():
        position_list = value if isinstance(value, list) else [value]
        for pos in position_list:
            pos.setdefault("position_id", str(uuid.uuid4())[:8])
            pos.setdefault("opened_at", time.time())
            pos.setdefault("algo_id", "")
            pos.setdefault("highest_price_seen", pos["entry_price"])
        rm.open_positions[symbol] = position_list
    return rm


def dump_risk_manager(rm: RiskManager) -> dict:
    return {
        "starting_capital": rm.starting_capital,
        "current_balance": rm.current_balance,
        "trading_halted": rm.trading_halted,
        "strategy_paused": rm.strategy_paused,
        "pnl_history": rm.pnl_history,
        "open_positions": {
            symbol: [_position_to_dict(p) for p in positions]
            for symbol, positions in rm.open_positions.items()
        },
    }


def check_and_close_positions(client: OKXDemoClient, rm: RiskManager, strategy, strategy_name: str,
                               apply_trailing_stop: bool = True):
    """Vérifie CHAQUE position ouverte (il peut y en avoir jusqu'à 2 par crypto)."""
    for symbol in list(rm.open_positions.keys()):
        positions = list(rm.open_positions.get(symbol, []))
        if not positions:
            continue

        current_price = client.get_current_price(symbol)
        candles = client.get_candles(symbol, config.CANDLE_INTERVAL, config.CANDLES_HISTORY_LENGTH)
        current_signal = strategy.get_signal(candles)

        for pos in positions:
            position_id = pos["position_id"] if isinstance(pos, dict) else pos.position_id
            entry_price = pos["entry_price"] if isinstance(pos, dict) else pos.entry_price
            sl = pos["stop_loss_price"] if isinstance(pos, dict) else pos.stop_loss_price
            tp = pos["take_profit_price"] if isinstance(pos, dict) else pos.take_profit_price
            quantity = pos["quantity"] if isinstance(pos, dict) else pos.quantity
            algo_id = pos.get("algo_id", "") if isinstance(pos, dict) else getattr(pos, "algo_id", "")

            if current_price <= sl or current_price >= tp:
                reason = "stop_loss" if current_price <= sl else "take_profit"
                pnl = rm.close_position(symbol, current_price, position_id=position_id)
                log_event(strategy_name, symbol, "close", current_price, quantity, pnl, f"{reason} [{position_id}]")
                continue

            if apply_trailing_stop:
                new_sl = rm.compute_trailing_stop_update(pos, current_price)
                if new_sl is not None:
                    try:
                        client.update_stop_loss(symbol, algo_id, new_sl)
                        if isinstance(pos, dict):
                            pos["stop_loss_price"] = new_sl
                            pos["highest_price_seen"] = max(pos.get("highest_price_seen", entry_price), current_price)
                        else:
                            pos.stop_loss_price = new_sl
                            pos.highest_price_seen = max(pos.highest_price_seen, current_price)
                        log_event(strategy_name, symbol, "trailing_stop_update", current_price, quantity,
                                  note=f"Nouveau SL={new_sl} [{position_id}]")
                    except Exception as e:
                        print(f"[main] Échec mise à jour trailing stop pour {symbol} : {e}")

            exit_reason = rm.check_exit_conditions_for_position(pos, current_price, current_signal.direction)
            if exit_reason:
                client.place_market_sell(symbol, quantity)
                pnl = rm.close_position(symbol, current_price, position_id=position_id)
                log_event(strategy_name, symbol, "close", current_price, quantity, pnl, f"{exit_reason} [{position_id}]")


def run():
    client = OKXDemoClient()
    state = load_state()

    for strategy in ALL_STRATEGIES:
        rm = build_risk_manager(strategy.name, state[strategy.name])
        shadow_key = f"{strategy.name}__shadow"
        rm_shadow = build_risk_manager(shadow_key, state[shadow_key])

        check_and_close_positions(client, rm, strategy, strategy.name, apply_trailing_stop=True)
        check_and_close_positions(client, rm_shadow, strategy, shadow_key, apply_trailing_stop=False)

        for symbol in config.SYMBOLS:
            candles = client.get_candles(symbol, config.CANDLE_INTERVAL, config.CANDLES_HISTORY_LENGTH)
            signal = strategy.get_signal(candles)

            if not signal.is_actionable:
                continue

            current_price = client.get_current_price(symbol)

            shadow_direction = signal.direction
            shadow_score = signal.conviction_score
            if shadow_direction == "buy":
                allowed_shadow, _ = rm_shadow.can_open_position(symbol)
                if allowed_shadow:
                    atr_shadow = calculate_atr(candles["high"], candles["low"], candles["close"])
                    shadow_sizing = rm_shadow.calculate_position(symbol, current_price, atr_shadow)
                    if shadow_sizing:
                        rm_shadow.register_open_position(shadow_sizing)
                        log_event(shadow_key, symbol, "buy", current_price, shadow_sizing.quantity,
                                  note=f"score={shadow_score:.0f} (Niveau 1 seul, sans IA) [{shadow_sizing.position_id}]")
            elif shadow_direction == "sell" and symbol in rm_shadow.open_positions:
                for pos in list(rm_shadow.open_positions.get(symbol, [])):
                    position_id = pos.position_id if hasattr(pos, "position_id") else pos["position_id"]
                    quantity = pos.quantity if hasattr(pos, "quantity") else pos["quantity"]
                    pnl = rm_shadow.close_position(symbol, current_price, position_id=position_id)
                    log_event(shadow_key, symbol, "sell_signal_close", current_price, quantity, pnl, f"[{position_id}]")

            higher_tf_context = build_higher_timeframe_context(client, symbol)
            signal = evaluate(symbol, strategy.name, signal, current_price, market_context=higher_tf_context)

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
                sizing = rm.calculate_position(
                    symbol, current_price, atr, entry_reasons=signal.reasons,
                    ai_size_factor=signal.ai_size_factor,
                    ai_sl_tightness=signal.ai_sl_tightness,
                    ai_tp_extension=signal.ai_tp_extension,
                )
                if sizing is None:
                    continue

                client.place_market_buy(symbol, sizing.quantity)
                algo_id = client.place_stop_loss_take_profit(
                    symbol, sizing.quantity, sizing.stop_loss_price, sizing.take_profit_price
                )
                sizing.algo_id = algo_id
                rm.register_open_position(sizing)
                note = f"score={signal.conviction_score:.0f} | SL={sizing.stop_loss_price} TP={sizing.take_profit_price} [{sizing.position_id}]"
                if signal.ai_reviewed:
                    note += f" | IA: {signal.ai_notes} (taille x{signal.ai_size_factor:.2f})"
                log_event(strategy.name, symbol, "buy", current_price, sizing.quantity, note=note)

            elif signal.direction == "sell" and symbol in rm.open_positions:
                for pos in list(rm.open_positions.get(symbol, [])):
                    position_id = pos["position_id"] if isinstance(pos, dict) else pos.position_id
                    quantity = pos["quantity"] if isinstance(pos, dict) else pos.quantity
                    client.place_market_sell(symbol, quantity)
                    pnl = rm.close_position(symbol, current_price, position_id=position_id)
                    log_event(strategy.name, symbol, "sell_signal_close", current_price, quantity, pnl, f"[{position_id}]")

        state[strategy.name] = dump_risk_manager(rm)
        state[shadow_key] = dump_risk_manager(rm_shadow)

    save_state(state)
    print("Exécution terminée :", datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    run()
