"""
risk_manager.py
================
Le "cerveau prudent" du bot. Ce module ne décide JAMAIS d'acheter ou de
vendre (ça, c'est le rôle des stratégies + du decision_engine) -- il
décide UNIQUEMENT :
  - combien d'argent on a le droit de risquer sur un trade
  - où placer le stop-loss et le take-profit
  - QUAND fermer une position (SL/TP touché, signal invalidé, ou trade
    "mort" qui ne bouge plus après trop longtemps)
  - si le bot a le droit d'ouvrir une nouvelle position (circuit breaker)
  - si une stratégie doit être mise en pause (dégradation de performance)

Tout est recalculé en fonction du SOLDE ACTUEL, jamais d'un montant fixe.
"""

from dataclasses import dataclass, field
from typing import Optional
import time
import numpy as np

import performance_tracker


# ----------------------------------------------------------------------
# PARAMÈTRES DE RISQUE
# ----------------------------------------------------------------------
MAX_POSITION_PCT = 0.10          # 10% max du solde actuel par position
CIRCUIT_BREAKER_LOSS_PCT = 0.20  # arrêt de sécurité si -20% du capital de départ
MAX_OPEN_POSITIONS_PER_SYMBOL = 1

ATR_STOP_LOSS_MULTIPLIER = 1.5
ATR_TAKE_PROFIT_MULTIPLIER = 3.0

MAX_POSITION_AGE_HOURS = 72   # sortie temporelle : ferme un trade "mort" après 3 jours
STAGNANT_MOVE_THRESHOLD = 0.01  # si le prix a bougé de moins de 1% après ce délai -> on considère le trade mort


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
    opened_at: float = field(default_factory=time.time)   # timestamp d'ouverture
    entry_reasons: list = field(default_factory=list)      # pour vérifier l'invalidation plus tard


class RiskManager:
    def __init__(self, strategy_name: str, starting_capital: float):
        self.strategy_name = strategy_name
        self.starting_capital = starting_capital
        self.current_balance = starting_capital
        self.open_positions = {}
        self.trading_halted = False       # circuit breaker (perte cumulée)
        self.strategy_paused = False      # pause pour dégradation de performance
        self.pnl_history = []             # historique glissant pour performance_tracker

    # ------------------------------------------------------------------
    # 1. CIRCUIT BREAKER (perte cumulée)
    # ------------------------------------------------------------------
    def check_circuit_breaker(self) -> bool:
        loss_pct = (self.starting_capital - self.current_balance) / self.starting_capital
        if loss_pct >= CIRCUIT_BREAKER_LOSS_PCT:
            self.trading_halted = True
        return self.trading_halted

    def update_balance(self, new_balance: float):
        self.current_balance = new_balance
        self.check_circuit_breaker()

    # ------------------------------------------------------------------
    # 2. SUIVI DE PERFORMANCE (dégradation -> pause automatique)
    # ------------------------------------------------------------------
    def record_trade_result(self, pnl: float):
        self.pnl_history = performance_tracker.update_pnl_history(self.pnl_history, pnl)
        stats = performance_tracker.compute_rolling_stats(self.pnl_history)
        self.strategy_paused = stats["should_pause"]
        return stats

    # ------------------------------------------------------------------
    # 3. VÉRIFICATIONS AVANT D'OUVRIR UNE POSITION
    # ------------------------------------------------------------------
    def can_open_position(self, symbol: str) -> tuple[bool, str]:
        if self.trading_halted:
            return False, "Circuit breaker actif : perte cumulée >= seuil"
        if self.strategy_paused:
            return False, "Stratégie en pause : performance récente dégradée"
        if symbol in self.open_positions:
            return False, f"Une position sur {symbol} est déjà ouverte"
        if self.current_balance <= 0:
            return False, "Solde épuisé"
        return True, "OK"

    # ------------------------------------------------------------------
    # 4. CALCUL DE LA TAILLE DE POSITION + SL/TP DYNAMIQUES
    # ------------------------------------------------------------------
    def calculate_position(self, symbol: str, entry_price: float, atr: float,
                            entry_reasons: list = None) -> Optional[PositionSizing]:
        allowed, reason = self.can_open_position(symbol)
        if not allowed:
            return None

        position_size_usd = self.current_balance * MAX_POSITION_PCT
        quantity = position_size_usd / entry_price

        stop_loss_price = entry_price - (atr * ATR_STOP_LOSS_MULTIPLIER)
        take_profit_price = entry_price + (atr * ATR_TAKE_PROFIT_MULTIPLIER)

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

    # ------------------------------------------------------------------
    # 5. ENREGISTRER / FERMER UNE POSITION
    # ------------------------------------------------------------------
    def register_open_position(self, sizing: PositionSizing):
        self.open_positions[sizing.symbol] = sizing

    def close_position(self, symbol: str, exit_price: float) -> float:
        if symbol not in self.open_positions:
            return 0.0
        pos = self.open_positions.pop(symbol)
        entry_price = pos["entry_price"] if isinstance(pos, dict) else pos.entry_price
        quantity = pos["quantity"] if isinstance(pos, dict) else pos.quantity
        pnl = quantity * (exit_price - entry_price)
        self.update_balance(self.current_balance + pnl)
        self.record_trade_result(pnl)
        return round(pnl, 2)

    # ------------------------------------------------------------------
    # 6. SORTIES INTELLIGENTES (au-delà du simple SL/TP)
    # ------------------------------------------------------------------
    def check_exit_conditions(self, symbol: str, current_price: float,
                               current_signal_direction: str = None) -> Optional[str]:
        """
        Vérifie si une position doit être fermée pour une raison AUTRE que
        SL/TP (ces deux-là sont gérés directement par l'ordre OCO côté
        OKX). Retourne la raison de sortie, ou None si la position doit
        rester ouverte.
        """
        if symbol not in self.open_positions:
            return None
        pos = self.open_positions[symbol]

        # Sortie sur invalidation : le signal d'origine (ex: tendance
        # haussière) s'est inversé avant même que le SL/TP soit touché.
        if current_signal_direction == "sell":
            return "invalidation"

        # Sortie temporelle : le trade est "mort" -- ouvert depuis
        # longtemps sans bouger significativement.
        opened_at = pos["opened_at"] if isinstance(pos, dict) else pos.opened_at
        entry_price = pos["entry_price"] if isinstance(pos, dict) else pos.entry_price
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
