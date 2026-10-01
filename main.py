"""
main.py
=======
Le chef d'orchestre. Ce script tourne toutes les heures via GitHub
Actions. À chaque exécution, pour chaque stratégie, TROIS comptes
virtuels indépendants sont mis à jour :

  1. Compte RÉEL (spot, avec IA)      -- state[strategy.name]
  2. Compte FANTÔME (spot, sans IA)   -- state[strategy.name + "__shadow"]
  3. Compte FUTURES (levier, avec IA) -- state[strategy.name + "__futures"]

Les 3 utilisent le MÊME signal technique (Niveau 1) au départ, mais
divergent ensuite : le fantôme ignore l'IA, le compte Futures utilise de
vrais contrats perpétuels démo OKX à effet de levier (risque en dollars
gardé identique au spot, voir risk_manager.calculate_leveraged_position).
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
import notifications
import memory
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


def build_market_regime_summary(client: OKXDemoClient) -> str:
    """
    Vision globale du PANIER de cryptos suivies (pas du marché crypto
    entier -- une nuance importante) : quelle proportion est actuellement
    au-dessus de sa moyenne longue, et quelle est la variation moyenne du
    panier. Calculé UNE SEULE FOIS par exécution (pas par stratégie) pour
    éviter de multiplier les appels API inutilement, puis réutilisé pour
    toutes les décisions de cette exécution.
    """
    try:
        above_count, total = 0, 0
        changes = []
        for symbol in config.SYMBOLS:
            candles = client.get_candles(symbol, config.CANDLE_INTERVAL, config.CANDLES_HISTORY_LENGTH)
            closes = candles["close"]
            if len(closes) < 51:
                continue
            long_ma = moving_average(closes, 50)
            total += 1
            if closes[-1] > long_ma:
                above_count += 1
            changes.append((closes[-1] - closes[-20]) / closes[-20] * 100)

        if total == 0:
            return ""

        pct_above = (above_count / total) * 100
        avg_change = sum(changes) / len(changes) if changes else 0.0
        bias = "haussier" if pct_above > 60 else ("baissier" if pct_above < 40 else "neutre/mixte")

        return (
            f"Vision globale du panier de {total} cryptos suivies par ce bot (pas le marché crypto "
            f"entier) : {above_count}/{total} ({pct_above:.0f}%) au-dessus de leur moyenne longue "
            f"-> biais {bias} sur ce panier. Variation moyenne du panier sur 20 bougies : {avg_change:+.1f}%."
        )
    except Exception as e:
        print(f"[main] Vision de marché globale indisponible : {e}")
        return ""


def _current_month_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _new_strategy_state(starting_capital: float = None) -> dict:
    capital = starting_capital if starting_capital is not None else config.STARTING_CAPITAL_PER_STRATEGY
    return {
        "starting_capital": capital,
        "current_balance": capital,
        "trading_halted": False,
        "strategy_paused": False,
        "pnl_history": [],
        "open_positions": {},
        "month_label": _current_month_label(),
        "month_start_balance": capital,
        "profit_ceiling_hit": False,
    }


def load_state() -> dict:
    if os.path.exists(config.STATE_FILE):
        with open(config.STATE_FILE, "r") as f:
            state = json.load(f)
        for strat in ALL_STRATEGIES:
            state.setdefault(f"{strat.name}__shadow", _new_strategy_state())
            if config.USE_FUTURES:
                state.setdefault(f"{strat.name}__futures", _new_strategy_state(config.FUTURES_STARTING_CAPITAL_PER_STRATEGY))
        return state

    state = {}
    for strat in ALL_STRATEGIES:
        state[strat.name] = _new_strategy_state()
        state[f"{strat.name}__shadow"] = _new_strategy_state()
        if config.USE_FUTURES:
            state[f"{strat.name}__futures"] = _new_strategy_state(config.FUTURES_STARTING_CAPITAL_PER_STRATEGY)
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
        "leverage": pos.leverage,
        "margin_usd": pos.margin_usd,
    }


def build_risk_manager(strategy_name: str, saved: dict) -> RiskManager:
    rm = RiskManager(
        strategy_name, saved["starting_capital"],
        month_label=saved.get("month_label"),
        month_start_balance=saved.get("month_start_balance"),
        profit_ceiling_hit=saved.get("profit_ceiling_hit", False),
    )
    rm.current_balance = saved["current_balance"]
    rm.trading_halted = saved["trading_halted"]
    rm.strategy_paused = saved.get("strategy_paused", False)
    rm.pnl_history = saved.get("pnl_history", [])
    rm.check_monthly_rollover(_current_month_label())   # réinitialise le plafond si nouveau mois

    raw_positions = saved.get("open_positions", {})
    rm.open_positions = {}
    for symbol, value in raw_positions.items():
        position_list = value if isinstance(value, list) else [value]
        for pos in position_list:
            pos.setdefault("position_id", str(uuid.uuid4())[:8])
            pos.setdefault("opened_at", time.time())
            pos.setdefault("algo_id", "")
            pos.setdefault("highest_price_seen", pos["entry_price"])
            pos.setdefault("leverage", 1.0)
            pos.setdefault("margin_usd", pos.get("position_size_usd", 0.0))
        rm.open_positions[symbol] = position_list
    return rm


def dump_risk_manager(rm: RiskManager) -> dict:
    return {
        "starting_capital": rm.starting_capital,
        "current_balance": rm.current_balance,
        "trading_halted": rm.trading_halted,
        "strategy_paused": rm.strategy_paused,
        "pnl_history": rm.pnl_history,
        "month_label": rm.month_label,
        "month_start_balance": rm.month_start_balance,
        "profit_ceiling_hit": rm.profit_ceiling_hit,
        "open_positions": {
            symbol: [_position_to_dict(p) for p in positions]
            for symbol, positions in rm.open_positions.items()
        },
    }


def check_and_close_positions(client: OKXDemoClient, rm: RiskManager, strategy, strategy_name: str,
                               apply_trailing_stop: bool = True, notify: bool = False):
    """Vérifie CHAQUE position ouverte (compte spot, réel ou fantôme)."""
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
                if notify:
                    notifications.notify_trade_closed(strategy_name, symbol, current_price, pnl, reason)
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
                if notify:
                    notifications.notify_trade_closed(strategy_name, symbol, current_price, pnl, exit_reason)


def check_and_close_futures_positions(client: OKXDemoClient, rm: RiskManager, strategy,
                                       strategy_name: str, futures_symbol_map: dict, notify: bool = True):
    """
    Équivalent de check_and_close_positions() mais pour le compte Futures :
    utilise les méthodes OKX spécifiques aux contrats perpétuels et
    convertit la quantité en nombre de contrats avant chaque appel à OKX.
    """
    for spot_symbol in list(rm.open_positions.keys()):
        positions = list(rm.open_positions.get(spot_symbol, []))
        if not positions:
            continue

        futures_symbol = futures_symbol_map[spot_symbol]
        current_price = client.get_current_price(futures_symbol)
        candles = client.get_candles(spot_symbol, config.CANDLE_INTERVAL, config.CANDLES_HISTORY_LENGTH)
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
                real_pnl = None
                try:
                    real_pnl = client.get_futures_realized_pnl(futures_symbol)
                except Exception as e:
                    print(f"[main] PnL réel OKX indisponible pour {futures_symbol}, repli sur calcul interne : {e}")
                pnl = rm.close_position(spot_symbol, current_price, position_id=position_id, pnl_override=real_pnl)
                log_event(strategy_name, spot_symbol, "close", current_price, quantity, pnl, f"{reason} [{position_id}]")
                if notify:
                    notifications.notify_trade_closed(strategy_name, spot_symbol, current_price, pnl, reason)
                continue

            new_sl = rm.compute_trailing_stop_update(pos, current_price)
            if new_sl is not None:
                try:
                    client.update_futures_stop_loss(futures_symbol, algo_id, new_sl)
                    if isinstance(pos, dict):
                        pos["stop_loss_price"] = new_sl
                        pos["highest_price_seen"] = max(pos.get("highest_price_seen", entry_price), current_price)
                    else:
                        pos.stop_loss_price = new_sl
                        pos.highest_price_seen = max(pos.highest_price_seen, current_price)
                    log_event(strategy_name, spot_symbol, "trailing_stop_update", current_price, quantity,
                              note=f"Nouveau SL={new_sl} [{position_id}]")
                except Exception as e:
                    print(f"[main] Échec mise à jour trailing stop Futures pour {futures_symbol} : {e}")

            exit_reason = rm.check_exit_conditions_for_position(pos, current_price, current_signal.direction)
            if exit_reason:
                real_pnl = None
                try:
                    contracts = client.convert_to_contracts(futures_symbol, quantity)
                    client.place_futures_close_long(futures_symbol, contracts, config.FUTURES_MARGIN_MODE)
                    real_pnl = client.get_futures_realized_pnl(futures_symbol)
                except Exception as e:
                    print(f"[main] Échec fermeture ou récupération PnL Futures pour {futures_symbol} : {e}")
                pnl = rm.close_position(spot_symbol, current_price, position_id=position_id, pnl_override=real_pnl)
                log_event(strategy_name, spot_symbol, "close", current_price, quantity, pnl, f"{exit_reason} [{position_id}]")
                if notify:
                    notifications.notify_trade_closed(strategy_name, spot_symbol, current_price, pnl, exit_reason)


def run():
    client = OKXDemoClient()
    state = load_state()

    # Calculé UNE SEULE FOIS pour toute l'exécution (pas par stratégie) --
    # évite de multiplier les appels API, et le régime du panier ne change
    # de toute façon pas d'une stratégie à l'autre dans la même heure.
    market_regime_summary = build_market_regime_summary(client)

    for strategy in ALL_STRATEGIES:
        rm = build_risk_manager(strategy.name, state[strategy.name])
        shadow_key = f"{strategy.name}__shadow"
        rm_shadow = build_risk_manager(shadow_key, state[shadow_key])

        futures_key = f"{strategy.name}__futures"
        rm_futures = build_risk_manager(futures_key, state[futures_key]) if config.USE_FUTURES else None

        was_halted = rm.trading_halted
        was_paused = rm.strategy_paused
        was_ceiling_hit = rm.profit_ceiling_hit

        check_and_close_positions(client, rm, strategy, strategy.name, apply_trailing_stop=True, notify=True)
        check_and_close_positions(client, rm_shadow, strategy, shadow_key, apply_trailing_stop=False, notify=False)
        if config.USE_FUTURES:
            check_and_close_futures_positions(client, rm_futures, strategy, futures_key, config.FUTURES_SYMBOLS, notify=True)

        for symbol in config.SYMBOLS:
            candles = client.get_candles(symbol, config.CANDLE_INTERVAL, config.CANDLES_HISTORY_LENGTH)
            signal = strategy.get_signal(candles)

            if not signal.is_actionable:
                continue

            current_price = client.get_current_price(symbol)

            # --- Compte fantôme (Niveau 1 seul, sans IA) ---
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
            combined_market_context = "\n\n".join(filter(None, [higher_tf_context, market_regime_summary]))

            # --- Compte Futures (levier, avec IA -- évaluation séparée du spot) ---
            if config.USE_FUTURES and signal.direction == "buy":
                futures_symbol = config.FUTURES_SYMBOLS[symbol]
                futures_signal = strategy.get_signal(candles)   # copie indépendante, pas mutée par l'éval du spot
                futures_memory_context = memory.get_memory_context(futures_key, symbol)
                futures_signal = evaluate(
                    futures_symbol, strategy.name, futures_signal, current_price,
                    market_context=combined_market_context, leverage=config.FUTURES_LEVERAGE,
                    memory_context=futures_memory_context,
                )
                if futures_signal.is_actionable and futures_signal.direction == "buy":
                    allowed_f, reason_f = rm_futures.can_open_position(symbol)
                    if not allowed_f:
                        log_event(futures_key, symbol, "skip_buy", current_price, note=reason_f)
                    else:
                        atr_f = calculate_atr(candles["high"], candles["low"], candles["close"])
                        sizing_f = rm_futures.calculate_leveraged_position(
                            symbol, current_price, atr_f, leverage=config.FUTURES_LEVERAGE,
                            entry_reasons=futures_signal.reasons,
                            ai_size_factor=futures_signal.ai_size_factor,
                            ai_sl_tightness=futures_signal.ai_sl_tightness,
                            ai_tp_extension=futures_signal.ai_tp_extension,
                        )
                        if sizing_f is not None:
                            try:
                                client.set_leverage(futures_symbol, config.FUTURES_LEVERAGE, config.FUTURES_MARGIN_MODE)
                                contracts = client.convert_to_contracts(futures_symbol, sizing_f.quantity)
                                client.place_futures_open_long(futures_symbol, contracts, config.FUTURES_MARGIN_MODE)
                                algo_id_f = client.place_futures_stop_loss_take_profit(
                                    futures_symbol, contracts, sizing_f.stop_loss_price,
                                    sizing_f.take_profit_price, config.FUTURES_MARGIN_MODE,
                                )
                                sizing_f.algo_id = algo_id_f
                                rm_futures.register_open_position(sizing_f)
                                note = (f"score={futures_signal.conviction_score:.0f} | levier={config.FUTURES_LEVERAGE}x "
                                        f"| marge={sizing_f.margin_usd}$ | SL={sizing_f.stop_loss_price} TP={sizing_f.take_profit_price} "
                                        f"[{sizing_f.position_id}]")
                                if futures_signal.ai_reviewed:
                                    note += f" | IA: {futures_signal.ai_notes}"
                                log_event(futures_key, symbol, "buy", current_price, sizing_f.quantity, note=note)
                                notifications.notify_trade_opened(futures_key, symbol, current_price,
                                                                   note=f"Levier {config.FUTURES_LEVERAGE}x, marge {sizing_f.margin_usd}$")
                            except Exception as e:
                                print(f"[main] Échec ouverture Futures pour {futures_symbol} : {e}")
                elif not futures_signal.is_actionable:
                    log_event(futures_key, symbol, "signal_rejected", current_price,
                              note="; ".join(futures_signal.reasons))

            elif config.USE_FUTURES and signal.direction == "sell" and symbol in rm_futures.open_positions:
                futures_symbol = config.FUTURES_SYMBOLS[symbol]
                for pos in list(rm_futures.open_positions.get(symbol, [])):
                    position_id = pos["position_id"] if isinstance(pos, dict) else pos.position_id
                    quantity = pos["quantity"] if isinstance(pos, dict) else pos.quantity
                    real_pnl = None
                    try:
                        contracts = client.convert_to_contracts(futures_symbol, quantity)
                        client.place_futures_close_long(futures_symbol, contracts, config.FUTURES_MARGIN_MODE)
                        real_pnl = client.get_futures_realized_pnl(futures_symbol)
                    except Exception as e:
                        print(f"[main] Échec fermeture ou récupération PnL Futures (signal vente) pour {futures_symbol} : {e}")
                    pnl = rm_futures.close_position(symbol, current_price, position_id=position_id, pnl_override=real_pnl)
                    log_event(futures_key, symbol, "sell_signal_close", current_price, quantity, pnl, f"[{position_id}]")
                    notifications.notify_trade_closed(futures_key, symbol, current_price, pnl, "signal_vente")

            # --- Compte RÉEL spot (avec IA) ---
            spot_memory_context = memory.get_memory_context(strategy.name, symbol)
            signal = evaluate(symbol, strategy.name, signal, current_price,
                               market_context=combined_market_context, memory_context=spot_memory_context)

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
                notifications.notify_trade_opened(strategy.name, symbol, current_price,
                                                   note=f"Score: {signal.conviction_score:.0f}/100")

            elif signal.direction == "sell" and symbol in rm.open_positions:
                for pos in list(rm.open_positions.get(symbol, [])):
                    position_id = pos["position_id"] if isinstance(pos, dict) else pos.position_id
                    quantity = pos["quantity"] if isinstance(pos, dict) else pos.quantity
                    client.place_market_sell(symbol, quantity)
                    pnl = rm.close_position(symbol, current_price, position_id=position_id)
                    log_event(strategy.name, symbol, "sell_signal_close", current_price, quantity, pnl, f"[{position_id}]")
                    notifications.notify_trade_closed(strategy.name, symbol, current_price, pnl, "signal_vente")

        if rm.trading_halted and not was_halted:
            notifications.notify_circuit_breaker(strategy.name)
        if rm.strategy_paused and not was_paused:
            notifications.notify_strategy_paused(strategy.name)
        if rm.profit_ceiling_hit and not was_ceiling_hit:
            notifications.notify_profit_ceiling(strategy.name)

        state[strategy.name] = dump_risk_manager(rm)
        state[shadow_key] = dump_risk_manager(rm_shadow)
        if config.USE_FUTURES:
            state[futures_key] = dump_risk_manager(rm_futures)

    save_state(state)
    print("Exécution terminée :", datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    run()
