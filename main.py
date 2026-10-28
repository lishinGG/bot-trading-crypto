"""
main.py -- orchestrateur. 3 pistes par stratégie :
  - spot (avec IA)            : toujours LONG
  - spot__shadow (sans IA)    : toujours LONG, référence pure Niveau 1
  - futures (avec IA, levier) : LONG et SHORT

Une stratégie peut déclarer son propre timeframe (ex. "4H"). Elle n'est alors évaluée qu'UNE FOIS par
nouvelle bougie clôturée (curseur dans state.json), même si le bot tourne toutes les heures ; la
gestion des positions ouvertes (SL/TP, trailing, invalidation) reste, elle, évaluée à chaque exécution.
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
from signal import Signal
import notifications
import memory
import config


class _CandleCache:
    """Évite de re-télécharger les mêmes bougies plusieurs fois dans une même exécution."""

    def __init__(self, client):
        self._client = client
        self._cache = {}

    def get_candles(self, symbol, bar="1H", limit=100):
        key = (symbol, bar, limit)
        if key not in self._cache:
            self._cache[key] = self._client.get_candles(symbol, bar, limit)
        return self._cache[key]

    def __getattr__(self, name):
        return getattr(self._client, name)


def build_higher_timeframe_context(client, symbol: str) -> str:
    try:
        summary_lines = []
        for label, bar in [("4h", "4H"), ("1 jour", "1D")]:
            candles = client.get_candles(symbol, bar, config.CANDLES_HISTORY_LENGTH)
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


def compute_regime_stats(client):
    """(nb de cryptos au-dessus de leur moyenne longue 1D, total, variations 20 bougies) sur le panier."""
    try:
        bullish, total, changes = 0, 0, []
        for symbol in config.SYMBOLS:
            closes = client.get_candles(symbol, "1D", config.CANDLES_HISTORY_LENGTH)["close"]
            if len(closes) < 21:
                continue
            long_ma = moving_average(closes, min(50, len(closes)))
            total += 1
            if closes[-1] > long_ma:
                bullish += 1
            changes.append((closes[-1] - closes[-20]) / closes[-20] * 100)
        return bullish, total, changes
    except Exception as e:
        print(f"[main] Vision marché global indisponible : {e}")
        return 0, 0, []


def format_regime_summary(bullish, total, changes) -> str:
    if total == 0:
        return ""
    pct = (bullish / total) * 100
    if pct >= 60:
        regime = "biais haussier sur ce panier"
    elif pct <= 40:
        regime = "biais baissier sur ce panier"
    else:
        regime = "mixte / sans direction claire sur ce panier"
    avg_change = sum(changes) / len(changes) if changes else 0
    return (f"Vision globale du panier de {total} cryptos suivies par ce bot (pas le marché crypto entier) : "
            f"{bullish}/{total} ({pct:.0f}%) au-dessus de leur moyenne longue -> {regime}. "
            f"Variation moyenne du panier sur 20 bougies : {avg_change:+.1f}%.")


def _new_strategy_state() -> dict:
    return {
        "starting_capital": config.STARTING_CAPITAL_PER_STRATEGY,
        "current_balance": config.STARTING_CAPITAL_PER_STRATEGY,
        "trading_halted": False,
        "strategy_paused": False,
        "pnl_history": [],
        "open_positions": {},
        "current_month": None,
        "month_start_balance": config.STARTING_CAPITAL_PER_STRATEGY,
        "profit_ceiling_hit": False,
    }


def load_state() -> dict:
    state = {}
    if os.path.exists(config.STATE_FILE):
        with open(config.STATE_FILE, "r") as f:
            state = json.load(f)
    for strat in ALL_STRATEGIES:
        state.setdefault(strat.name, _new_strategy_state())
        state.setdefault(f"{strat.name}__shadow", _new_strategy_state())
        if config.USE_FUTURES:
            state.setdefault(f"{strat.name}__futures", _new_strategy_state())
    return state


def save_state(state: dict):
    with open(config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def log_event(strategy_name, symbol, action, price, quantity=0.0, pnl=0.0, note=""):
    file_exists = os.path.exists(config.LOG_FILE)
    with open(config.LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "strategy", "symbol", "action", "price", "quantity", "pnl", "note"])
        writer.writerow([datetime.now(timezone.utc).isoformat(), strategy_name, symbol, action, price, quantity, pnl, note])


def _position_to_dict(pos) -> dict:
    if isinstance(pos, dict):
        return pos
    return {
        "position_id": pos.position_id, "entry_price": pos.entry_price, "quantity": pos.quantity,
        "stop_loss_price": pos.stop_loss_price, "take_profit_price": pos.take_profit_price,
        "opened_at": pos.opened_at, "algo_id": pos.algo_id, "highest_price_seen": pos.highest_price_seen,
        "leverage": pos.leverage, "margin_usd": pos.margin_usd, "side": pos.side,
        "atr_at_entry": pos.atr_at_entry, "trail_mode": pos.trail_mode, "trail_ts": pos.trail_ts,
    }


def build_risk_manager(strategy_name: str, saved: dict) -> RiskManager:
    rm = RiskManager(strategy_name, saved["starting_capital"])
    rm.current_balance = saved["current_balance"]
    rm.trading_halted = saved["trading_halted"]
    rm.strategy_paused = saved.get("strategy_paused", False)
    rm.pnl_history = saved.get("pnl_history", [])
    rm.current_month = saved.get("current_month")
    rm.month_start_balance = saved.get("month_start_balance", saved["starting_capital"])
    rm.profit_ceiling_hit = saved.get("profit_ceiling_hit", False)

    rm.open_positions = {}
    for symbol, value in saved.get("open_positions", {}).items():
        position_list = value if isinstance(value, list) else [value]
        for pos in position_list:
            pos.setdefault("position_id", str(uuid.uuid4())[:8])
            pos.setdefault("opened_at", time.time())
            pos.setdefault("algo_id", "")
            pos.setdefault("highest_price_seen", pos["entry_price"])
            pos.setdefault("leverage", 1.0)
            pos.setdefault("margin_usd", 0.0)
            pos.setdefault("side", "long")
            pos.setdefault("atr_at_entry", 0.0)
            pos.setdefault("trail_mode", "constant")
            pos.setdefault("trail_ts", 0)
        rm.open_positions[symbol] = position_list
    return rm


def dump_risk_manager(rm: RiskManager) -> dict:
    return {
        "starting_capital": rm.starting_capital, "current_balance": rm.current_balance,
        "trading_halted": rm.trading_halted, "strategy_paused": rm.strategy_paused,
        "pnl_history": rm.pnl_history, "current_month": rm.current_month,
        "month_start_balance": rm.month_start_balance, "profit_ceiling_hit": rm.profit_ceiling_hit,
        "open_positions": {
            symbol: [_position_to_dict(p) for p in positions]
            for symbol, positions in rm.open_positions.items()
        },
    }


def _get_field(pos, name, default=None):
    return pos[name] if isinstance(pos, dict) else getattr(pos, name, default)


def _set_field(pos, name, value):
    if isinstance(pos, dict):
        pos[name] = value
    else:
        setattr(pos, name, value)


def _exit_direction(strategy, candles):
    """Direction utilisée pour l'invalidation. Une stratégie peut fournir sa propre règle (exit_direction)."""
    fn = getattr(strategy, "exit_direction", None)
    if fn is not None:
        return fn(candles)
    return strategy.get_signal(candles).direction


def _atr_for(signal, candles):
    a = getattr(signal, "atr", 0.0)
    return a if a and a > 0 else calculate_atr(candles["high"], candles["low"], candles["close"])


def _trailing_result(rm, pos, candles, current_price, allow_constant):
    """Retourne (résultat, mode, ts_bougie). Le mode 'atr_stage' est évalué sur la clôture 4h."""
    mode = _get_field(pos, "trail_mode", "constant")
    candle_ts = int(candles["ts"][-1]) if "ts" in candles and len(candles["ts"]) else 0
    if mode == "atr_stage":
        return rm.compute_staged_trailing_update(pos, float(candles["close"][-1]), candle_ts), mode, candle_ts
    if allow_constant:
        return rm.compute_trailing_stop_update(pos, current_price), mode, candle_ts
    return None, mode, candle_ts


# ----------------------------------------------------------------------
# SPOT (et compte fantôme) -- toujours LONG
# ----------------------------------------------------------------------
def check_and_close_positions(client, rm, strategy, strategy_name, apply_trailing_stop=True, notify=False):
    tf = getattr(strategy, "timeframe", config.CANDLE_INTERVAL)
    for symbol in list(rm.open_positions.keys()):
        positions = list(rm.open_positions.get(symbol, []))
        if not positions:
            continue
        current_price = client.get_current_price(symbol)
        candles = client.get_candles(symbol, tf, config.CANDLES_HISTORY_LENGTH)
        exit_dir = _exit_direction(strategy, candles)

        for pos in positions:
            position_id = _get_field(pos, "position_id")
            sl = _get_field(pos, "stop_loss_price")
            tp = _get_field(pos, "take_profit_price")
            quantity = _get_field(pos, "quantity")
            algo_id = _get_field(pos, "algo_id", "")

            if current_price <= sl or current_price >= tp:
                reason = "stop_loss" if current_price <= sl else "take_profit"
                pnl = rm.close_position(symbol, current_price, position_id=position_id)
                log_event(strategy_name, symbol, "close", current_price, quantity, pnl, f"{reason} [{position_id}]")
                if notify:
                    notifications.notify_trade_closed(strategy_name, symbol, current_price, pnl, reason)
                continue

            trail_result, mode, candle_ts = _trailing_result(rm, pos, candles, current_price, apply_trailing_stop)
            if trail_result is not None:
                new_sl, new_extreme = trail_result
                try:
                    client.update_stop_loss(symbol, algo_id, new_sl)
                    _set_field(pos, "stop_loss_price", new_sl)
                    _set_field(pos, "highest_price_seen", new_extreme)
                    if mode == "atr_stage":
                        _set_field(pos, "trail_ts", candle_ts)
                    log_event(strategy_name, symbol, "trailing_stop_update", current_price, quantity,
                              note=f"Nouveau SL={new_sl} [{position_id}]")
                except Exception as e:
                    print(f"[main] Échec mise à jour trailing stop pour {symbol} : {e}")

            exit_reason = rm.check_exit_conditions_for_position(pos, current_price, exit_dir)
            if exit_reason:
                client.place_market_sell(symbol, quantity)
                pnl = rm.close_position(symbol, current_price, position_id=position_id)
                log_event(strategy_name, symbol, "close", current_price, quantity, pnl, f"{exit_reason} [{position_id}]")
                if notify:
                    notifications.notify_trade_closed(strategy_name, symbol, current_price, pnl, exit_reason)


# ----------------------------------------------------------------------
# FUTURES -- long ET short
# ----------------------------------------------------------------------
def check_and_close_futures_positions(client, rm, strategy, strategy_name):
    tf = getattr(strategy, "timeframe", config.CANDLE_INTERVAL)
    for spot_symbol in list(rm.open_positions.keys()):
        positions = list(rm.open_positions.get(spot_symbol, []))
        if not positions:
            continue
        futures_symbol = config.FUTURES_SYMBOLS[spot_symbol]
        current_price = client.get_current_price(futures_symbol)
        candles = client.get_candles(spot_symbol, tf, config.CANDLES_HISTORY_LENGTH)
        exit_dir = _exit_direction(strategy, candles)

        for pos in positions:
            position_id = _get_field(pos, "position_id")
            sl = _get_field(pos, "stop_loss_price")
            tp = _get_field(pos, "take_profit_price")
            quantity = _get_field(pos, "quantity")
            algo_id = _get_field(pos, "algo_id", "")
            side = _get_field(pos, "side", "long")

            hit_sl = (current_price <= sl) if side == "long" else (current_price >= sl)
            hit_tp = (current_price >= tp) if side == "long" else (current_price <= tp)
            if hit_sl or hit_tp:
                reason = "stop_loss" if hit_sl else "take_profit"
                real_pnl = None
                try:
                    real_pnl = client.get_futures_realized_pnl(futures_symbol)
                except Exception as e:
                    print(f"[main] PnL réel OKX indisponible pour {futures_symbol} : {e}")
                pnl = rm.close_position(spot_symbol, current_price, position_id=position_id, pnl_override=real_pnl)
                log_event(strategy_name, spot_symbol, "close", current_price, quantity, pnl, f"{reason} [{side}] [{position_id}]")
                notifications.notify_trade_closed(strategy_name, spot_symbol, current_price, pnl, reason)
                continue

            trail_result, mode, candle_ts = _trailing_result(rm, pos, candles, current_price, True)
            if trail_result is not None:
                new_sl, new_extreme = trail_result
                try:
                    client.update_futures_stop_loss(futures_symbol, algo_id, new_sl)
                    _set_field(pos, "stop_loss_price", new_sl)
                    _set_field(pos, "highest_price_seen", new_extreme)
                    if mode == "atr_stage":
                        _set_field(pos, "trail_ts", candle_ts)
                    log_event(strategy_name, spot_symbol, "trailing_stop_update", current_price, quantity,
                              note=f"Nouveau SL={new_sl} [{side}] [{position_id}]")
                except Exception as e:
                    print(f"[main] Échec trailing stop Futures pour {futures_symbol} : {e}")

            exit_reason = rm.check_exit_conditions_for_position(pos, current_price, exit_dir)
            if exit_reason:
                real_pnl = None
                try:
                    contracts = client.convert_to_contracts(futures_symbol, quantity)
                    if side == "long":
                        client.place_futures_close_long(futures_symbol, contracts, config.FUTURES_MARGIN_MODE)
                    else:
                        client.place_futures_close_short(futures_symbol, contracts, config.FUTURES_MARGIN_MODE)
                    real_pnl = client.get_futures_realized_pnl(futures_symbol)
                except Exception as e:
                    print(f"[main] Échec fermeture/PnL Futures pour {futures_symbol} : {e}")
                pnl = rm.close_position(spot_symbol, current_price, position_id=position_id, pnl_override=real_pnl)
                log_event(strategy_name, spot_symbol, "close", current_price, quantity, pnl, f"{exit_reason} [{side}] [{position_id}]")
                notifications.notify_trade_closed(strategy_name, spot_symbol, current_price, pnl, exit_reason)


def _open_futures_position(client, rm_futures, strategy_name, futures_key, symbol, futures_symbol,
                            side, current_price, atr, futures_signal, trail_mode="constant"):
    allowed, reason = rm_futures.can_open_position(symbol, side=side)
    if not allowed:
        log_event(futures_key, symbol, "skip_open", current_price, note=reason)
        return

    sizing = rm_futures.calculate_leveraged_position(
        symbol, current_price, atr, leverage=config.FUTURES_LEVERAGE, side=side,
        entry_reasons=futures_signal.reasons,
        ai_size_factor=futures_signal.ai_size_factor,
        ai_sl_tightness=futures_signal.ai_sl_tightness,
        ai_tp_extension=futures_signal.ai_tp_extension,
        trail_mode=trail_mode,
    )
    if sizing is None:
        return

    client.set_leverage(futures_symbol, config.FUTURES_LEVERAGE, config.FUTURES_MARGIN_MODE)
    contracts = client.convert_to_contracts(futures_symbol, sizing.quantity)
    try:
        if side == "long":
            client.place_futures_open_long(futures_symbol, contracts, config.FUTURES_MARGIN_MODE)
        else:
            client.place_futures_open_short(futures_symbol, contracts, config.FUTURES_MARGIN_MODE)
        algo_id = client.place_futures_stop_loss_take_profit(
            futures_symbol, contracts, sizing.stop_loss_price, sizing.take_profit_price,
            config.FUTURES_MARGIN_MODE, side=side,
        )
        sizing.algo_id = algo_id
    except Exception as e:
        print(f"[main] Échec ouverture Futures pour {futures_symbol} : {e}")
        return

    rm_futures.register_open_position(sizing)
    note = (f"score={futures_signal.conviction_score:.0f} | side={side} | "
            f"SL={sizing.stop_loss_price} TP={sizing.take_profit_price} [{sizing.position_id}]")
    if futures_signal.ai_reviewed:
        note += f" | IA: {futures_signal.ai_notes} (taille x{futures_signal.ai_size_factor:.2f})"
    log_event(futures_key, symbol, "buy" if side == "long" else "sell", current_price, sizing.quantity, note=note)
    notifications.notify_trade_opened(futures_key, symbol, current_price,
                                       note=f"{side.upper()} | Score: {futures_signal.conviction_score:.0f}/100")


def _close_all_futures_positions(client, rm_futures, strategy_name, futures_key, symbol, futures_symbol, current_price):
    for pos in list(rm_futures.open_positions.get(symbol, [])):
        position_id = _get_field(pos, "position_id")
        quantity = _get_field(pos, "quantity")
        side = _get_field(pos, "side", "long")
        real_pnl = None
        try:
            contracts = client.convert_to_contracts(futures_symbol, quantity)
            if side == "long":
                client.place_futures_close_long(futures_symbol, contracts, config.FUTURES_MARGIN_MODE)
            else:
                client.place_futures_close_short(futures_symbol, contracts, config.FUTURES_MARGIN_MODE)
            real_pnl = client.get_futures_realized_pnl(futures_symbol)
        except Exception as e:
            print(f"[main] Échec fermeture (signal opposé) Futures pour {futures_symbol} : {e}")
        pnl = rm_futures.close_position(symbol, current_price, position_id=position_id, pnl_override=real_pnl)
        log_event(futures_key, symbol, "signal_close", current_price, quantity, pnl, f"[{side}] [{position_id}]")
        notifications.notify_trade_closed(futures_key, symbol, current_price, pnl, "signal_opposé")


def _copy_signal(signal):
    return Signal(direction=signal.direction, conviction_score=signal.conviction_score,
                  reasons=list(signal.reasons), atr=getattr(signal, "atr", 0.0))


def run():
    client = _CandleCache(OKXDemoClient())
    state = load_state()
    current_month_str = datetime.now(timezone.utc).strftime("%Y-%m")
    signal_cursor = state.setdefault("_meta", {}).setdefault("signal_cursor", {})

    bullish, total, changes = compute_regime_stats(client)
    regime_pct = (bullish / total * 100) if total else None
    market_regime_summary = format_regime_summary(bullish, total, changes)

    for strategy in ALL_STRATEGIES:
        tf = getattr(strategy, "timeframe", config.CANDLE_INTERVAL)
        trail_mode = getattr(strategy, "trail_mode", "constant")

        rm = build_risk_manager(strategy.name, state[strategy.name])
        shadow_key = f"{strategy.name}__shadow"
        rm_shadow = build_risk_manager(shadow_key, state[shadow_key])
        futures_key = f"{strategy.name}__futures"
        rm_futures = build_risk_manager(futures_key, state[futures_key]) if config.USE_FUTURES else None
        all_rms = [rm, rm_shadow] + ([rm_futures] if rm_futures else [])

        max_age = getattr(strategy, "max_position_age_hours", None)
        for r in all_rms:
            if max_age:
                r.max_position_age_hours = max_age
            r.check_monthly_rollover(current_month_str)
            r.check_monthly_ceiling()

        was_halted, was_paused, was_ceiling = rm.trading_halted, rm.strategy_paused, rm.profit_ceiling_hit

        check_and_close_positions(client, rm, strategy, strategy.name, apply_trailing_stop=True, notify=True)
        check_and_close_positions(client, rm_shadow, strategy, shadow_key, apply_trailing_stop=False, notify=False)
        if rm_futures:
            check_and_close_futures_positions(client, rm_futures, strategy, futures_key)

        for symbol in config.SYMBOLS:
            candles = client.get_candles(symbol, tf, config.CANDLES_HISTORY_LENGTH)

            # Stratégie sur un timeframe plus lent : on n'évalue chaque nouvelle bougie qu'une seule fois.
            if tf != config.CANDLE_INTERVAL and "ts" in candles and len(candles["ts"]):
                last_ts = int(candles["ts"][-1])
                cursor_key = f"{strategy.name}|{symbol}"
                if signal_cursor.get(cursor_key) == last_ts:
                    continue
                signal_cursor[cursor_key] = last_ts

            context = None
            if getattr(strategy, "needs_context", False):
                context = {"daily": client.get_candles(symbol, "1D", config.CANDLES_HISTORY_LENGTH),
                           "regime_bullish_pct": regime_pct}
            signal = strategy.get_signal(candles, context=context)
            if not signal.is_actionable:
                continue

            current_price = client.get_current_price(symbol)
            higher_tf_context = build_higher_timeframe_context(client, symbol)
            combined_market_context = "\n\n".join(filter(None, [higher_tf_context, market_regime_summary]))

            # --- Compte fantôme (Niveau 1 pur, toujours LONG) ---
            if signal.direction == "buy":
                allowed_shadow, _ = rm_shadow.can_open_position(symbol, side="long")
                if allowed_shadow:
                    shadow_sizing = rm_shadow.calculate_position(symbol, current_price, _atr_for(signal, candles),
                                                                 trail_mode=trail_mode)
                    if shadow_sizing:
                        rm_shadow.register_open_position(shadow_sizing)
                        log_event(shadow_key, symbol, "buy", current_price, shadow_sizing.quantity,
                                  note=f"score={signal.conviction_score:.0f} (Niveau 1 seul) [{shadow_sizing.position_id}]")
            elif signal.direction == "sell" and symbol in rm_shadow.open_positions:
                for pos in list(rm_shadow.open_positions.get(symbol, [])):
                    position_id = _get_field(pos, "position_id")
                    quantity = _get_field(pos, "quantity")
                    pnl = rm_shadow.close_position(symbol, current_price, position_id=position_id)
                    log_event(shadow_key, symbol, "sell_signal_close", current_price, quantity, pnl, f"[{position_id}]")

            # --- Spot réel (avec IA, toujours LONG) ---
            spot_memory_context = memory.get_memory_context(strategy.name, symbol)
            spot_signal = evaluate(symbol, strategy.name, _copy_signal(signal), current_price,
                                    market_context=combined_market_context, memory_context=spot_memory_context,
                                    is_futures=False, side="long")

            if not spot_signal.is_actionable:
                log_event(strategy.name, symbol, "signal_rejected", current_price, note="; ".join(spot_signal.reasons))
            elif spot_signal.direction == "buy":
                allowed, reason = rm.can_open_position(symbol, side="long")
                if not allowed:
                    log_event(strategy.name, symbol, "skip_buy", current_price, note=reason)
                else:
                    sizing = rm.calculate_position(
                        symbol, current_price, _atr_for(signal, candles), entry_reasons=spot_signal.reasons,
                        ai_size_factor=spot_signal.ai_size_factor, ai_sl_tightness=spot_signal.ai_sl_tightness,
                        ai_tp_extension=spot_signal.ai_tp_extension, trail_mode=trail_mode,
                    )
                    if sizing is not None:
                        client.place_market_buy(symbol, sizing.quantity)
                        algo_id = client.place_stop_loss_take_profit(symbol, sizing.quantity, sizing.stop_loss_price, sizing.take_profit_price)
                        sizing.algo_id = algo_id
                        rm.register_open_position(sizing)
                        note = f"score={spot_signal.conviction_score:.0f} | SL={sizing.stop_loss_price} TP={sizing.take_profit_price} [{sizing.position_id}]"
                        if spot_signal.ai_reviewed:
                            note += f" | IA: {spot_signal.ai_notes} (taille x{spot_signal.ai_size_factor:.2f})"
                        log_event(strategy.name, symbol, "buy", current_price, sizing.quantity, note=note)
                        notifications.notify_trade_opened(strategy.name, symbol, current_price, note=f"Score: {spot_signal.conviction_score:.0f}/100")
            elif spot_signal.direction == "sell" and symbol in rm.open_positions:
                for pos in list(rm.open_positions.get(symbol, [])):
                    position_id = _get_field(pos, "position_id")
                    quantity = _get_field(pos, "quantity")
                    client.place_market_sell(symbol, quantity)
                    pnl = rm.close_position(symbol, current_price, position_id=position_id)
                    log_event(strategy.name, symbol, "sell_signal_close", current_price, quantity, pnl)
                    notifications.notify_trade_closed(strategy.name, symbol, current_price, pnl, "signal_vente")

            # --- Futures (avec IA, levier, LONG et SHORT) ---
            if rm_futures:
                futures_symbol = config.FUTURES_SYMBOLS[symbol]
                existing_positions = rm_futures.open_positions.get(symbol, [])
                existing_side = _get_field(existing_positions[0], "side", "long") if existing_positions else None

                futures_action, intended_side = None, None
                if signal.direction == "buy":
                    if existing_side == "short":
                        futures_action = "close"
                    else:
                        futures_action, intended_side = "open", "long"
                elif signal.direction == "sell":
                    if existing_side == "long":
                        futures_action = "close"
                    elif config.FUTURES_ALLOW_SHORT:
                        futures_action, intended_side = "open", "short"

                if futures_action == "close":
                    _close_all_futures_positions(client, rm_futures, futures_key, futures_key, symbol, futures_symbol, current_price)
                elif futures_action == "open":
                    futures_memory_context = memory.get_memory_context(futures_key, symbol)
                    futures_signal = evaluate(symbol, strategy.name, _copy_signal(signal), current_price,
                                               market_context=combined_market_context, memory_context=futures_memory_context,
                                               is_futures=True, leverage=config.FUTURES_LEVERAGE, side=intended_side)
                    if not futures_signal.is_actionable:
                        log_event(futures_key, symbol, "signal_rejected", current_price, note="; ".join(futures_signal.reasons))
                    else:
                        _open_futures_position(client, rm_futures, futures_key, futures_key, symbol, futures_symbol,
                                                intended_side, current_price, _atr_for(signal, candles), futures_signal,
                                                trail_mode=trail_mode)

        if rm.trading_halted and not was_halted:
            notifications.notify_circuit_breaker(strategy.name)
        if rm.strategy_paused and not was_paused:
            notifications.notify_strategy_paused(strategy.name)
        if rm.profit_ceiling_hit and not was_ceiling:
            notifications.notify_profit_ceiling(strategy.name)

        state[strategy.name] = dump_risk_manager(rm)
        state[shadow_key] = dump_risk_manager(rm_shadow)
        if rm_futures:
            state[futures_key] = dump_risk_manager(rm_futures)

    save_state(state)
    print("Exécution terminée :", datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    run()
