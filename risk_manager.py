"""risk_manager.py"""
from dataclasses import dataclass, field
from typing import Optional
import time
import uuid
import numpy as np

import performance_tracker

MAX_POSITION_PCT = 0.10
CIRCUIT_BREAKER_LOSS_PCT = 0.20
MAX_OPEN_POSITIONS_PER_SYMBOL = 2
MAX_TOTAL_OPEN_POSITIONS = 3

ATR_STOP_LOSS_MULTIPLIER = 1.5
ATR_TAKE_PROFIT_MULTIPLIER = 3.0

MIN_AI_SIZE_FACTOR = 0.3
MIN_AI_SL_TIGHTNESS = 0.6
MAX_AI_TP_EXTENSION = 2.0

MAX_POSITION_AGE_HOURS = 72
STAGNANT_MOVE_THRESHOLD = 0.01
TRAILING_STOP_MIN_PROGRESS_PCT = 0.005


@dataclass
class PositionSizing:
    symbol: str
    entry_price: float
    position_size_usd: float
    quantity: float
    stop_loss_price: float
    take_profit_price: float
    risk_amount_usd: float
    reward_amount_usd: float
    risk_reward_ratio: float
    position_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    opened_at: float = field(default_factory=time.time)
    entry_reasons: list = field(default_factory=list)
    algo_id: str = ""
    highest_price_seen: float = 0.0
    leverage: float = 1.0          # 1.0 = spot (pas de levier)
    margin_usd: float = 0.0        # marge réellement immobilisée (= position_size_usd si spot)


def _clamp(value, low, high):
    return max(low, min(high, value))


class RiskManager:
    def __init__(self, strategy_name, starting_capital):
        self.strategy_name = strategy_name
        self.starting_capital = starting_capital
        self.current_balance = starting_capital
        self.open_positions = {}
        self.trading_halted = False
        self.strategy_paused = False
        self.pnl_history = []

    def check_circuit_breaker(self):
        loss_pct = (self.starting_capital - self.current_balance) / self.starting_capital
        if loss_pct >= CIRCUIT_BREAKER_LOSS_PCT:
            self.trading_halted = True
        return self.trading_halted

    def update_balance(self, new_balance):
        self.current_balance = new_balance
        self.check_circuit_breaker()

    def record_trade_result(self, pnl):
        self.pnl_history = performance_tracker.update_pnl_history(self.pnl_history, pnl)
        stats = performance_tracker.compute_rolling_stats(self.pnl_history)
        self.strategy_paused = stats["should_pause"]
        return stats

    def _position_count(self, symbol):
        return len(self.open_positions.get(symbol, []))

    def _total_position_count(self):
        return sum(len(v) for v in self.open_positions.values())

    def can_open_position(self, symbol):
        if self.trading_halted:
            return False, "Circuit breaker actif : perte cumulée >= seuil"
        if self.strategy_paused:
            return False, "Stratégie en pause : performance récente dégradée"
        if self._total_position_count() >= MAX_TOTAL_OPEN_POSITIONS:
            return False, f"Limite d'exposition globale atteinte ({MAX_TOTAL_OPEN_POSITIONS} positions max)"
        if self._position_count(symbol) >= MAX_OPEN_POSITIONS_PER_SYMBOL:
            return False, f"Déjà {MAX_OPEN_POSITIONS_PER_SYMBOL} position(s) ouverte(s) sur {symbol}"
        if self.current_balance <= 0:
            return False, "Solde épuisé"
        return True, "OK"

    def calculate_position(self, symbol, entry_price, atr, entry_reasons=None,
                            ai_size_factor=1.0, ai_sl_tightness=1.0, ai_tp_extension=1.0):
        allowed, reason = self.can_open_position(symbol)
        if not allowed:
            return None

        safe_size_factor = _clamp(ai_size_factor, MIN_AI_SIZE_FACTOR, 1.0)
        safe_sl_tightness = _clamp(ai_sl_tightness, MIN_AI_SL_TIGHTNESS, 1.0)
        safe_tp_extension = _clamp(ai_tp_extension, 1.0, MAX_AI_TP_EXTENSION)

        position_size_usd = self.current_balance * MAX_POSITION_PCT * safe_size_factor
        quantity = position_size_usd / entry_price

        sl_distance = atr * ATR_STOP_LOSS_MULTIPLIER * safe_sl_tightness
        tp_distance = atr * ATR_TAKE_PROFIT_MULTIPLIER * safe_tp_extension

        stop_loss_price = entry_price - sl_distance
        take_profit_price = entry_price + tp_distance

        risk_amount_usd = quantity * (entry_price - stop_loss_price)
        reward_amount_usd = quantity * (take_profit_price - entry_price)
        risk_reward_ratio = reward_amount_usd / risk_amount_usd if risk_amount_usd > 0 else 0

        return PositionSizing(
            symbol=symbol, entry_price=entry_price,
            position_size_usd=round(position_size_usd, 2), quantity=round(quantity, 8),
            stop_loss_price=round(stop_loss_price, 4), take_profit_price=round(take_profit_price, 4),
            risk_amount_usd=round(risk_amount_usd, 2), reward_amount_usd=round(reward_amount_usd, 2),
            risk_reward_ratio=round(risk_reward_ratio, 2), entry_reasons=entry_reasons or [],
            highest_price_seen=entry_price,
        )

    def calculate_leveraged_position(self, symbol, entry_price, atr, leverage, entry_reasons=None,
                                      ai_size_factor=1.0, ai_sl_tightness=1.0, ai_tp_extension=1.0):
        """
        Calcule une position À LEVIER (Futures) en gardant EXACTEMENT le
        même risque en dollars que ce que ferait calculate_position() en
        spot, dans TOUS les cas -- pas seulement les cas "normaux".

        Principe : on utilise le MÊME notionnel que ce que le spot
        utiliserait comme taille de position (10% du solde), donc le même
        risque en dollars en découle automatiquement (même formule, même
        quantité). Le levier sert UNIQUEMENT à réduire la marge
        nécessaire pour tenir ce même notionnel -- jamais à l'augmenter.
        C'est plus simple et plus sûr qu'une logique de "risque cible
        avec plafond" qui peut se retourner contre nous sur des cas
        limites (stop-loss très serré).
        """
        allowed, reason = self.can_open_position(symbol)
        if not allowed:
            return None

        safe_size_factor = _clamp(ai_size_factor, MIN_AI_SIZE_FACTOR, 1.0)
        safe_sl_tightness = _clamp(ai_sl_tightness, MIN_AI_SL_TIGHTNESS, 1.0)
        safe_tp_extension = _clamp(ai_tp_extension, 1.0, MAX_AI_TP_EXTENSION)

        # EXACTEMENT la même formule de notionnel que calculate_position()
        # en spot -- garantit le même risque en dollars par construction,
        # quelle que soit la distance du SL.
        notional_used = self.current_balance * MAX_POSITION_PCT * safe_size_factor
        margin_used = notional_used / leverage   # le levier réduit la marge, jamais le notionnel

        quantity = notional_used / entry_price

        sl_distance = atr * ATR_STOP_LOSS_MULTIPLIER * safe_sl_tightness
        tp_distance = atr * ATR_TAKE_PROFIT_MULTIPLIER * safe_tp_extension
        stop_loss_price = entry_price - sl_distance
        take_profit_price = entry_price + tp_distance

        risk_amount_usd = quantity * sl_distance
        reward_amount_usd = quantity * tp_distance
        risk_reward_ratio = reward_amount_usd / risk_amount_usd if risk_amount_usd > 0 else 0

        return PositionSizing(
            symbol=symbol, entry_price=entry_price,
            position_size_usd=round(notional_used, 2), quantity=round(quantity, 8),
            stop_loss_price=round(stop_loss_price, 4), take_profit_price=round(take_profit_price, 4),
            risk_amount_usd=round(risk_amount_usd, 2), reward_amount_usd=round(reward_amount_usd, 2),
            risk_reward_ratio=round(risk_reward_ratio, 2), entry_reasons=entry_reasons or [],
            highest_price_seen=entry_price, leverage=leverage, margin_usd=round(margin_used, 2),
        )

    def register_open_position(self, sizing):
        self.open_positions.setdefault(sizing.symbol, []).append(sizing)

    def close_position(self, symbol, exit_price, position_id=None, pnl_override=None):
        """
        pnl_override : si fourni (ex: le vrai P&L renvoyé par OKX pour les
        Futures, frais et funding déjà déduits), il est utilisé TEL QUEL
        à la place du calcul interne simplifié (quantité x variation de
        prix), qui ne connaît ni les frais ni le funding.
        """
        positions = self.open_positions.get(symbol, [])
        if not positions:
            return 0.0
        if position_id:
            match = next((p for p in positions if (p["position_id"] if isinstance(p, dict) else p.position_id) == position_id), None)
        else:
            match = positions[0]
        if match is None:
            return 0.0
        positions.remove(match)
        if not positions:
            del self.open_positions[symbol]

        if pnl_override is not None:
            pnl = pnl_override
        else:
            entry_price = match["entry_price"] if isinstance(match, dict) else match.entry_price
            quantity = match["quantity"] if isinstance(match, dict) else match.quantity
            pnl = quantity * (exit_price - entry_price)

        self.update_balance(self.current_balance + pnl)
        self.record_trade_result(pnl)
        return round(pnl, 2)

    def check_exit_conditions_for_position(self, position, current_price, current_signal_direction=None):
        if current_signal_direction == "sell":
            return "invalidation"
        opened_at = position["opened_at"] if isinstance(position, dict) else getattr(position, "opened_at", time.time())
        entry_price = position["entry_price"] if isinstance(position, dict) else position.entry_price
        age_hours = (time.time() - opened_at) / 3600
        if age_hours >= MAX_POSITION_AGE_HOURS:
            price_move_pct = abs(current_price - entry_price) / entry_price
            if price_move_pct < STAGNANT_MOVE_THRESHOLD:
                return "stagnation_temporelle"
        return None

    def compute_trailing_stop_update(self, position, current_price):
        entry_price = position["entry_price"] if isinstance(position, dict) else position.entry_price
        current_sl = position["stop_loss_price"] if isinstance(position, dict) else position.stop_loss_price
        highest_seen = position.get("highest_price_seen", entry_price) if isinstance(position, dict) \
            else getattr(position, "highest_price_seen", entry_price)
        original_sl_distance = entry_price - current_sl if current_sl < entry_price else 0
        if original_sl_distance <= 0:
            return None
        new_highest = max(highest_seen, current_price)
        if new_highest <= highest_seen * (1 + TRAILING_STOP_MIN_PROGRESS_PCT):
            return None
        candidate_new_sl = new_highest - original_sl_distance
        if candidate_new_sl > current_sl:
            return round(candidate_new_sl, 4)
        return None


def calculate_atr(high, low, close, period=14):
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return float(np.mean(tr[-period:]))
