"""
risk_manager.py
================
Taille de position, SL/TP, sorties, circuit breaker, plafond de profit mensuel, sens long/short
(short = Futures uniquement). Garde-fous IA : l'IA ne peut jamais augmenter le risque.

Deux modes de trailing :
  - "constant"  : le SL suit le plus haut/bas atteint à distance constante (stratégies 1h)
  - "atr_stage" : break-even à +1,5 ATR puis trailing 1 ATR, évalués à la CLÔTURE 4h,
                  avec l'ATR d'entrée (stratégie Trend Pullback 4H)
"""

from dataclasses import dataclass, field
from typing import Optional
import time
import uuid
import numpy as np
from datetime import datetime, timezone

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

MONTHLY_PROFIT_CEILING_PCT = 0.15

STAGED_BREAKEVEN_TRIGGER_ATR = 1.5
STAGED_TRAIL_ATR = 1.0


def _clamp(value, low, high):
    return max(low, min(high, value))


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
    leverage: float = 1.0
    margin_usd: float = 0.0
    side: str = "long"
    atr_at_entry: float = 0.0
    trail_mode: str = "constant"
    trail_ts: int = 0   # horodatage de la dernière bougie 4h déjà traitée par le trailing par paliers


class RiskManager:
    def __init__(self, strategy_name, starting_capital):
        self.strategy_name = strategy_name
        self.starting_capital = starting_capital
        self.current_balance = starting_capital
        self.open_positions: dict[str, list] = {}
        self.trading_halted = False
        self.strategy_paused = False
        self.pnl_history = []
        self.current_month = None
        self.month_start_balance = starting_capital
        self.profit_ceiling_hit = False
        self.max_position_age_hours = MAX_POSITION_AGE_HOURS

    # --- circuit breaker -------------------------------------------------
    def check_circuit_breaker(self):
        loss_pct = (self.starting_capital - self.current_balance) / self.starting_capital
        if loss_pct >= CIRCUIT_BREAKER_LOSS_PCT:
            self.trading_halted = True
        return self.trading_halted

    # --- plafond mensuel -------------------------------------------------
    def check_monthly_rollover(self, month_str: str = None) -> bool:
        month_now = month_str or datetime.now(timezone.utc).strftime("%Y-%m")
        if self.current_month != month_now:
            self.current_month = month_now
            self.month_start_balance = self.current_balance
            self.profit_ceiling_hit = False
            return True
        return False

    def check_monthly_ceiling(self):
        if self.month_start_balance <= 0:
            return self.profit_ceiling_hit
        gain_pct = (self.current_balance - self.month_start_balance) / self.month_start_balance
        if gain_pct >= MONTHLY_PROFIT_CEILING_PCT:
            self.profit_ceiling_hit = True
        return self.profit_ceiling_hit

    def update_balance(self, new_balance):
        self.current_balance = new_balance
        self.check_circuit_breaker()
        self.check_monthly_ceiling()

    def record_trade_result(self, pnl):
        self.pnl_history = performance_tracker.update_pnl_history(self.pnl_history, pnl)
        stats = performance_tracker.compute_rolling_stats(self.pnl_history)
        self.strategy_paused = stats["should_pause"]
        return stats

    # --- vérifications avant ouverture ----------------------------------
    def _position_count(self, symbol):
        return len(self.open_positions.get(symbol, []))

    def _total_position_count(self):
        return sum(len(v) for v in self.open_positions.values())

    def _opposite_side_open(self, symbol: str, side: str) -> bool:
        opposite = "short" if side == "long" else "long"
        for pos in self.open_positions.get(symbol, []):
            pos_side = pos["side"] if isinstance(pos, dict) else getattr(pos, "side", "long")
            if pos_side == opposite:
                return True
        return False

    def can_open_position(self, symbol, side: str = "long"):
        if self.trading_halted:
            return False, "Circuit breaker actif : perte cumulée >= seuil"
        if self.profit_ceiling_hit:
            return False, f"Plafond de profit mensuel atteint (+{MONTHLY_PROFIT_CEILING_PCT*100:.0f}%) : gains verrouillés jusqu'au mois prochain"
        if self.strategy_paused:
            return False, "Stratégie en pause : performance récente dégradée"
        if self._opposite_side_open(symbol, side):
            return False, f"Position {('short' if side=='long' else 'long')} opposée déjà ouverte sur {symbol} : fermeture prioritaire"
        if self._total_position_count() >= MAX_TOTAL_OPEN_POSITIONS:
            return False, f"Limite d'exposition globale atteinte ({MAX_TOTAL_OPEN_POSITIONS} positions max)"
        if self._position_count(symbol) >= MAX_OPEN_POSITIONS_PER_SYMBOL:
            return False, f"Déjà {MAX_OPEN_POSITIONS_PER_SYMBOL} position(s) ouverte(s) sur {symbol}"
        if self.current_balance <= 0:
            return False, "Solde épuisé"
        return True, "OK"

    # --- calcul de position : spot (toujours long) ----------------------
    def calculate_position(self, symbol, entry_price, atr, entry_reasons=None,
                            ai_size_factor=1.0, ai_sl_tightness=1.0, ai_tp_extension=1.0,
                            trail_mode="constant"):
        allowed, reason = self.can_open_position(symbol, side="long")
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
            highest_price_seen=entry_price, leverage=1.0, margin_usd=position_size_usd, side="long",
            atr_at_entry=atr, trail_mode=trail_mode,
        )

    # --- calcul de position : Futures (long ou short) -------------------
    def calculate_leveraged_position(self, symbol, entry_price, atr, leverage,
                                      side: str = "long", entry_reasons=None,
                                      ai_size_factor=1.0, ai_sl_tightness=1.0, ai_tp_extension=1.0,
                                      trail_mode="constant"):
        allowed, reason = self.can_open_position(symbol, side=side)
        if not allowed:
            return None
        safe_size_factor = _clamp(ai_size_factor, MIN_AI_SIZE_FACTOR, 1.0)
        safe_sl_tightness = _clamp(ai_sl_tightness, MIN_AI_SL_TIGHTNESS, 1.0)
        safe_tp_extension = _clamp(ai_tp_extension, 1.0, MAX_AI_TP_EXTENSION)

        position_size_usd = self.current_balance * MAX_POSITION_PCT * safe_size_factor
        quantity = position_size_usd / entry_price
        margin_usd = position_size_usd / leverage
        sl_distance = atr * ATR_STOP_LOSS_MULTIPLIER * safe_sl_tightness
        tp_distance = atr * ATR_TAKE_PROFIT_MULTIPLIER * safe_tp_extension

        if side == "long":
            stop_loss_price = entry_price - sl_distance
            take_profit_price = entry_price + tp_distance
        else:
            stop_loss_price = entry_price + sl_distance
            take_profit_price = entry_price - tp_distance

        risk_amount_usd = quantity * sl_distance
        reward_amount_usd = quantity * tp_distance
        risk_reward_ratio = reward_amount_usd / risk_amount_usd if risk_amount_usd > 0 else 0

        return PositionSizing(
            symbol=symbol, entry_price=entry_price,
            position_size_usd=round(position_size_usd, 2), quantity=round(quantity, 8),
            stop_loss_price=round(stop_loss_price, 4), take_profit_price=round(take_profit_price, 4),
            risk_amount_usd=round(risk_amount_usd, 2), reward_amount_usd=round(reward_amount_usd, 2),
            risk_reward_ratio=round(risk_reward_ratio, 2), entry_reasons=entry_reasons or [],
            highest_price_seen=entry_price, leverage=leverage, margin_usd=round(margin_usd, 2), side=side,
            atr_at_entry=atr, trail_mode=trail_mode,
        )

    # --- ouverture / fermeture ------------------------------------------
    def register_open_position(self, sizing: PositionSizing):
        self.open_positions.setdefault(sizing.symbol, []).append(sizing)

    def close_position(self, symbol, exit_price, position_id=None, pnl_override=None):
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
            side = match["side"] if isinstance(match, dict) else getattr(match, "side", "long")
            if side == "short":
                pnl = quantity * (entry_price - exit_price)
            else:
                pnl = quantity * (exit_price - entry_price)

        self.update_balance(self.current_balance + pnl)
        self.record_trade_result(pnl)
        return round(pnl, 2)

    # --- sorties intelligentes ------------------------------------------
    def check_exit_conditions_for_position(self, position, current_price, current_signal_direction=None):
        side = position["side"] if isinstance(position, dict) else getattr(position, "side", "long")
        if side == "long" and current_signal_direction == "sell":
            return "invalidation"
        if side == "short" and current_signal_direction == "buy":
            return "invalidation"

        opened_at = position["opened_at"] if isinstance(position, dict) else getattr(position, "opened_at", time.time())
        entry_price = position["entry_price"] if isinstance(position, dict) else position.entry_price
        age_hours = (time.time() - opened_at) / 3600
        if age_hours >= self.max_position_age_hours:
            price_move_pct = abs(current_price - entry_price) / entry_price
            if price_move_pct < STAGNANT_MOVE_THRESHOLD:
                return "stagnation_temporelle"
        return None

    # --- trailing "constant" (stratégies 1h) ----------------------------
    def compute_trailing_stop_update(self, position, current_price):
        side = position["side"] if isinstance(position, dict) else getattr(position, "side", "long")
        entry_price = position["entry_price"] if isinstance(position, dict) else position.entry_price
        current_sl = position["stop_loss_price"] if isinstance(position, dict) else position.stop_loss_price
        extreme_seen = position.get("highest_price_seen", entry_price) if isinstance(position, dict) \
            else getattr(position, "highest_price_seen", entry_price)

        if side == "long":
            original_sl_distance = entry_price - current_sl if current_sl < entry_price else 0
            if original_sl_distance <= 0:
                return None
            new_extreme = max(extreme_seen, current_price)
            if new_extreme <= extreme_seen * (1 + TRAILING_STOP_MIN_PROGRESS_PCT):
                return None
            candidate_new_sl = new_extreme - original_sl_distance
            if candidate_new_sl > current_sl:
                return round(candidate_new_sl, 4), round(new_extreme, 4)
            return None
        else:
            original_sl_distance = current_sl - entry_price if current_sl > entry_price else 0
            if original_sl_distance <= 0:
                return None
            new_extreme = min(extreme_seen, current_price)
            if new_extreme >= extreme_seen * (1 - TRAILING_STOP_MIN_PROGRESS_PCT):
                return None
            candidate_new_sl = new_extreme + original_sl_distance
            if candidate_new_sl < current_sl:
                return round(candidate_new_sl, 4), round(new_extreme, 4)
            return None

    # --- trailing "atr_stage" (Trend Pullback 4H) ------------------------
    def compute_staged_trailing_update(self, position, ref_close, candle_ts=0):
        """
        Étape 1 : dès qu'une clôture 4h est à +1,5 ATR (ATR d'entrée) de l'entrée, le SL passe au
        prix d'entrée (break-even). Étape 2 : aux clôtures 4h SUIVANTES, nouveau SL = clôture -/+ 1 ATR,
        uniquement s'il est plus serré. Le SL ne recule jamais. Retourne (nouveau_sl, extrême) ou None.
        """
        get = (lambda k, d=None: position.get(k, d)) if isinstance(position, dict) \
            else (lambda k, d=None: getattr(position, k, d))
        side, entry, sl = get("side", "long"), get("entry_price"), get("stop_loss_price")
        atr, trail_ts = get("atr_at_entry", 0.0), get("trail_ts", 0) or 0
        if not atr or atr <= 0:
            return None
        be_level = round(entry, 4)
        new_candle = (candle_ts == 0) or (candle_ts > trail_ts)

        if side == "long":
            if ref_close - entry < STAGED_BREAKEVEN_TRIGGER_ATR * atr:
                return None
            if sl < be_level - 1e-12:
                return be_level, round(ref_close, 4)
            if not new_candle:
                return None
            candidate = round(ref_close - STAGED_TRAIL_ATR * atr, 4)
            if candidate > sl:
                return candidate, round(ref_close, 4)
            return None

        if entry - ref_close < STAGED_BREAKEVEN_TRIGGER_ATR * atr:
            return None
        if sl > be_level + 1e-12:
            return be_level, round(ref_close, 4)
        if not new_candle:
            return None
        candidate = round(ref_close + STAGED_TRAIL_ATR * atr, 4)
        if candidate < sl:
            return candidate, round(ref_close, 4)
        return None


def calculate_atr(high, low, close, period=14):
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return float(np.mean(tr[-period:]))
