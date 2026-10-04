"""
risk_manager.py
================
Gère la taille de position, le SL/TP, les sorties, le circuit breaker,
le plafond de profit mensuel, et désormais le SENS de la position
(long ou short) -- le short n'existe que pour les Futures, jamais pour
le spot (structurellement impossible en spot).

GARDE-FOUS IA inchangés : l'IA ne peut jamais augmenter le risque au-delà
des plafonds fixés ici.

LONG vs SHORT -- ce qui change :
  - SL/TP : pour un long, le SL est EN DESSOUS de l'entrée, le TP AU-DESSUS.
    Pour un short, c'est l'inverse : le SL est AU-DESSUS, le TP EN DESSOUS
    (on gagne quand le prix baisse).
  - PnL : pour un long, profit = quantité x (sortie - entrée).
    Pour un short, profit = quantité x (entrée - sortie).
  - Trailing stop : pour un long, on suit le PLUS HAUT prix atteint et le
    SL ne fait que monter. Pour un short, on suit le PLUS BAS prix atteint
    et le SL ne fait que descendre.
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
    highest_price_seen: float = 0.0   # pour un short, sert de "plus bas prix vu" (nom conservé pour compat)
    leverage: float = 1.0
    margin_usd: float = 0.0
    side: str = "long"   # "long" ou "short" -- "long" toujours en spot


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

    # ------------------------------------------------------------------
    # CIRCUIT BREAKER
    # ------------------------------------------------------------------
    def check_circuit_breaker(self):
        loss_pct = (self.starting_capital - self.current_balance) / self.starting_capital
        if loss_pct >= CIRCUIT_BREAKER_LOSS_PCT:
            self.trading_halted = True
        return self.trading_halted

    # ------------------------------------------------------------------
    # PLAFOND DE PROFIT MENSUEL
    # ------------------------------------------------------------------
    def check_monthly_rollover(self, month_str: str = None) -> bool:
        """Retourne True si un nouveau mois vient de commencer (reset)."""
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

    # ------------------------------------------------------------------
    # VÉRIFICATIONS AVANT OUVERTURE
    # ------------------------------------------------------------------
    def _position_count(self, symbol):
        return len(self.open_positions.get(symbol, []))

    def _total_position_count(self):
        return sum(len(v) for v in self.open_positions.values())

    def _opposite_side_open(self, symbol: str, side: str) -> bool:
        """Vérifie si une position de sens OPPOSÉ est déjà ouverte sur ce
        symbole -- on ferme toujours l'opposée avant d'en ouvrir une
        nouvelle (pas de long ET short simultanés sur la même crypto)."""
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

    # ------------------------------------------------------------------
    # CALCUL DE POSITION -- SPOT (toujours long)
    # ------------------------------------------------------------------
    def calculate_position(self, symbol, entry_price, atr, entry_reasons=None,
                            ai_size_factor=1.0, ai_sl_tightness=1.0, ai_tp_extension=1.0):
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
        )

    # ------------------------------------------------------------------
    # CALCUL DE POSITION -- FUTURES (long OU short)
    # ------------------------------------------------------------------
    def calculate_leveraged_position(self, symbol, entry_price, atr, leverage,
                                      side: str = "long", entry_reasons=None,
                                      ai_size_factor=1.0, ai_sl_tightness=1.0, ai_tp_extension=1.0):
        allowed, reason = self.can_open_position(symbol, side=side)
        if not allowed:
            return None

        safe_size_factor = _clamp(ai_size_factor, MIN_AI_SIZE_FACTOR, 1.0)
        safe_sl_tightness = _clamp(ai_sl_tightness, MIN_AI_SL_TIGHTNESS, 1.0)
        safe_tp_extension = _clamp(ai_tp_extension, 1.0, MAX_AI_TP_EXTENSION)

        # Même notionnel et même risque en $ que le spot aurait utilisé --
        # le levier ne fait que réduire la marge nécessaire pour l'obtenir.
        position_size_usd = self.current_balance * MAX_POSITION_PCT * safe_size_factor
        quantity = position_size_usd / entry_price
        margin_usd = position_size_usd / leverage

        sl_distance = atr * ATR_STOP_LOSS_MULTIPLIER * safe_sl_tightness
        tp_distance = atr * ATR_TAKE_PROFIT_MULTIPLIER * safe_tp_extension

        if side == "long":
            stop_loss_price = entry_price - sl_distance
            take_profit_price = entry_price + tp_distance
        else:  # short : SL au-dessus de l'entrée, TP en dessous
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
        )

    # ------------------------------------------------------------------
    # OUVERTURE / FERMETURE
    # ------------------------------------------------------------------
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
                pnl = quantity * (entry_price - exit_price)   # profit quand le prix baisse
            else:
                pnl = quantity * (exit_price - entry_price)

        self.update_balance(self.current_balance + pnl)
        self.record_trade_result(pnl)
        return round(pnl, 2)

    # ------------------------------------------------------------------
    # SORTIES INTELLIGENTES
    # ------------------------------------------------------------------
    def check_exit_conditions_for_position(self, position, current_price, current_signal_direction=None):
        side = position["side"] if isinstance(position, dict) else getattr(position, "side", "long")

        # Invalidation : un signal qui va dans le sens OPPOSÉ à la position invalide celle-ci.
        if side == "long" and current_signal_direction == "sell":
            return "invalidation"
        if side == "short" and current_signal_direction == "buy":
            return "invalidation"

        opened_at = position["opened_at"] if isinstance(position, dict) else getattr(position, "opened_at", time.time())
        entry_price = position["entry_price"] if isinstance(position, dict) else position.entry_price
        age_hours = (time.time() - opened_at) / 3600
        if age_hours >= MAX_POSITION_AGE_HOURS:
            price_move_pct = abs(current_price - entry_price) / entry_price
            if price_move_pct < STAGNANT_MOVE_THRESHOLD:
                return "stagnation_temporelle"
        return None

    # ------------------------------------------------------------------
    # STOP-LOSS SUIVEUR (long et short)
    # ------------------------------------------------------------------
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
        else:  # short : on suit le plus BAS prix vu, le SL ne descend que vers le bas
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


def calculate_atr(high, low, close, period=14):
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return float(np.mean(tr[-period:]))
