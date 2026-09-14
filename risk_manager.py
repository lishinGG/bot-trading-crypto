"""
risk_manager.py
================
Le "cerveau prudent" du bot. Ce module décide :
  - combien d'argent on a le droit de risquer sur un trade
  - où placer le stop-loss et le take-profit
  - QUAND fermer une position (SL/TP touché, signal invalidé, ou trade
    "mort" qui ne bouge plus après trop longtemps)
  - si le bot a le droit d'ouvrir une nouvelle position (circuit breaker)
  - si une stratégie doit être mise en pause (dégradation de performance)

Tout est recalculé en fonction du SOLDE ACTUEL, jamais d'un montant fixe.

GARDE-FOUS IA : l'IA (Niveau 2) peut influencer la taille de position et
les distances de SL/TP via les facteurs ai_size_factor / ai_sl_tightness
/ ai_tp_extension du Signal -- mais UNIQUEMENT dans le sens qui RÉDUIT le
risque (taille plus petite, SL plus serré) ou qui n'AUGMENTE PAS le
risque (TP plus large, ce qui ne change pas ce qu'on peut perdre). Le
clampage de ces facteurs dans des bornes sûres est fait ICI, dans ce
fichier -- jamais laissé à la discrétion de l'IA elle-même.
"""

from dataclasses import dataclass, field
from typing import Optional
import time
import uuid
import numpy as np

import performance_tracker


# ----------------------------------------------------------------------
# PARAMÈTRES DE RISQUE
# ----------------------------------------------------------------------
MAX_POSITION_PCT = 0.10          # 10% max du solde actuel par position
CIRCUIT_BREAKER_LOSS_PCT = 0.20  # arrêt de sécurité si -20% du capital de départ
MAX_OPEN_POSITIONS_PER_SYMBOL = 2   # jusqu'à 2 positions simultanées sur la même crypto
MAX_TOTAL_OPEN_POSITIONS = 3        # jamais plus de 3 positions ouvertes en même temps, toutes cryptos confondues (diversification)

ATR_STOP_LOSS_MULTIPLIER = 1.5
ATR_TAKE_PROFIT_MULTIPLIER = 3.0

MIN_AI_SIZE_FACTOR = 0.3
MIN_AI_SL_TIGHTNESS = 0.6
MAX_AI_TP_EXTENSION = 2.0

MAX_POSITION_AGE_HOURS = 72
STAGNANT_MOVE_THRESHOLD = 0.01


@dataclass
class PositionSizing:
    """Résultat du calcul de taille de position pour un trade."""
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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class RiskManager:
    def __init__(self, strategy_name: str, starting_capital: float):
        self.strategy_name = strategy_name
        self.starting_capital = starting_capital
        self.current_balance = starting_capital
        self.open_positions: dict[str, list] = {}
        self.trading_halted = False
        self.strategy_paused = False
        self.pnl_history = []

    def check_circuit_breaker(self) -> bool:
        loss_pct = (self.starting_capital - self.current_balance) / self.starting_capital
        if loss_pct >= CIRCUIT_BREAKER_LOSS_PCT:
            self.trading_halted = True
        return self.trading_halted

    def update_balance(self, new_balance: float):
        self.current_balance = new_balance
        self.check_circuit_breaker()

    def record_trade_result(self, pnl: float):
        self.pnl_history = performance_tracker.update_pnl_history(self.pnl_history, pnl)
        stats = performance_tracker.compute_rolling_stats(self.pnl_history)
        self.strategy_paused = stats["should_pause"]
        return stats

    def _position_count(self, symbol: str) -> int:
        return len(self.open_positions.get(symbol, []))

    def _total_position_count(self) -> int:
        return sum(len(v) for v in self.open_positions.values())

    def can_open_position(self, symbol: str) -> tuple[bool, str]:
        if self.trading_halted:
            return False, "Circuit breaker actif : perte cumulée >= seuil"
        if self.strategy_paused:
            return False, "Stratégie en pause : performance récente dégradée"
        if self._total_position_count() >= MAX_TOTAL_OPEN_POSITIONS:
            return False, f"Limite d'exposition globale atteinte ({MAX_TOTAL_OPEN_POSITIONS} positions max, toutes cryptos confondues)"
        if self._position_count(symbol) >= MAX_OPEN_POSITIONS_PER_SYMBOL:
            return False, f"Déjà {MAX_OPEN_POSITIONS_PER_SYMBOL} position(s) ouverte(s) sur {symbol} (maximum)"
        if self.current_balance <= 0:
            return False, "Solde épuisé"
        return True, "OK"

    def calculate_position(self, symbol: str, entry_price: float, atr: float,
                            entry_reasons: list = None,
                            ai_size_factor: float = 1.0,
                            ai_sl_tightness: float = 1.0,
                            ai_tp_extension: float = 1.0) -> Optional[PositionSizing]:
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
            symbol=symbol,
            entry_price=entry_price,
            position_size_usd=round(position_size_usd, 2),
            quantity=round(quantity, 8),
            stop_loss_price=round(stop_loss_price, 4),
            take_profit_price=round(take_profit_price, 4),
            risk_amount_usd=round(risk_amount_usd, 2),
            reward_amount_usd=round(reward_amount_usd, 2),
            risk_reward_ratio=round(risk_reward_ratio, 2),
            entry_reasons=entry_reasons or [],
        )

    def register_open_position(self, sizing: PositionSizing):
        self.open_positions.setdefault(sizing.symbol, []).append(sizing)

    def close_position(self, symbol: str, exit_price: float, position_id: str = None) -> float:
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

        entry_price = match["entry_price"] if isinstance(match, dict) else match.entry_price
        quantity = match["quantity"] if isinstance(match, dict) else match.quantity
        pnl = quantity * (exit_price - entry_price)
        self.update_balance(self.current_balance + pnl)
        self.record_trade_result(pnl)
        return round(pnl, 2)

    def check_exit_conditions_for_position(self, position, current_price: float,
                                            current_signal_direction: str = None) -> Optional[str]:
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


def calculate_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    """Calcule l'ATR (Average True Range) — mesure standard de la volatilité."""
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)

    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]

    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close))
    )
    atr = np.mean(tr[-period:])
    return float(atr)
