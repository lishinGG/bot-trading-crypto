"""
risk_manager.py
================
Le "cerveau prudent" du bot. Ce module ne décide JAMAIS d'acheter ou de vendre
(ça, c'est le rôle des stratégies) — il décide UNIQUEMENT :
  - combien d'argent on a le droit de risquer sur un trade
  - où placer le stop-loss et le take-profit
  - si le bot a le droit d'ouvrir une nouvelle position ou non (circuit breaker)

Tout est recalculé en fonction du SOLDE ACTUEL, jamais d'un montant fixe.
Comme ça, que ton budget de départ soit 300$, 500$ ou 800$, la logique
s'adapte automatiquement.
"""

from dataclasses import dataclass
from typing import Optional
import numpy as np


# ----------------------------------------------------------------------
# PARAMÈTRES DE RISQUE (les seuls chiffres à ajuster si tu veux changer
# le comportement du bot — tout le reste se recalcule automatiquement)
# ----------------------------------------------------------------------
MAX_POSITION_PCT = 0.10          # 10% max du solde actuel par position
CIRCUIT_BREAKER_LOSS_PCT = 0.20  # arrêt de sécurité si -20% du capital de départ
MAX_OPEN_POSITIONS_PER_SYMBOL = 1  # 1 seule position par crypto à la fois

# Multiplicateurs pour le SL/TP dynamique, basés sur l'ATR (volatilité)
ATR_STOP_LOSS_MULTIPLIER = 1.5   # SL = 1.5x l'ATR sous le prix d'entrée
ATR_TAKE_PROFIT_MULTIPLIER = 3.0  # TP = 3x l'ATR au-dessus du prix d'entrée
# (ratio risque/récompense de base = 1:2, ajustable ci-dessus)


@dataclass
class PositionSizing:
    """Résultat du calcul de taille de position pour un trade."""
    symbol: str
    entry_price: float
    position_size_usd: float   # montant en dollars alloué à ce trade
    quantity: float             # quantité de crypto correspondante
    stop_loss_price: float
    take_profit_price: float
    risk_amount_usd: float      # combien on perd si le SL est touché
    reward_amount_usd: float    # combien on gagne si le TP est touché
    risk_reward_ratio: float


class RiskManager:
    def __init__(self, strategy_name: str, starting_capital: float):
        self.strategy_name = strategy_name
        self.starting_capital = starting_capital   # capital de départ (référence fixe pour le circuit breaker)
        self.current_balance = starting_capital     # solde actuel, mis à jour en continu
        self.open_positions = {}   # {symbol: position_info}
        self.trading_halted = False  # True si le circuit breaker est déclenché

    # ------------------------------------------------------------------
    # 1. CIRCUIT BREAKER — vérifie si on a le droit de continuer à trader
    # ------------------------------------------------------------------
    def check_circuit_breaker(self) -> bool:
        """
        Retourne True si le bot doit s'arrêter d'ouvrir de nouvelles positions.
        La perte est toujours calculée par rapport au capital de DÉPART de la
        session (référence fixe), mais le seuil se déclenche sur le solde ACTUEL.
        """
        loss_pct = (self.starting_capital - self.current_balance) / self.starting_capital
        if loss_pct >= CIRCUIT_BREAKER_LOSS_PCT:
            self.trading_halted = True
        return self.trading_halted

    def update_balance(self, new_balance: float):
        """À appeler après chaque trade fermé pour garder le solde à jour."""
        self.current_balance = new_balance
        self.check_circuit_breaker()

    # ------------------------------------------------------------------
    # 2. VÉRIFICATIONS AVANT D'OUVRIR UNE POSITION
    # ------------------------------------------------------------------
    def can_open_position(self, symbol: str) -> tuple[bool, str]:
        """
        Vérifie toutes les règles avant d'autoriser un nouveau trade.
        Retourne (autorisé: bool, raison: str)
        """
        if self.trading_halted:
            return False, "Circuit breaker actif : perte cumulée >= seuil, plus de nouvelles positions"

        if symbol in self.open_positions:
            return False, f"Une position sur {symbol} est déjà ouverte (max {MAX_OPEN_POSITIONS_PER_SYMBOL})"

        if self.current_balance <= 0:
            return False, "Solde épuisé"

        return True, "OK"

    # ------------------------------------------------------------------
    # 3. CALCUL DE LA TAILLE DE POSITION + SL/TP DYNAMIQUES
    # ------------------------------------------------------------------
    def calculate_position(self, symbol: str, entry_price: float, atr: float) -> Optional[PositionSizing]:
        """
        Calcule tout ce qu'il faut savoir pour ouvrir un trade proprement :
        - la taille de la position (en $ et en quantité)
        - le prix du stop-loss (basé sur la volatilité réelle, via l'ATR)
        - le prix du take-profit (basé sur la volatilité réelle, via l'ATR)

        L'ATR (Average True Range) mesure combien une crypto bouge en moyenne.
        Plus une crypto est volatile, plus le SL/TP sont placés loin du prix
        d'entrée — ça évite de se faire sortir par du "bruit" normal du marché.
        """
        allowed, reason = self.can_open_position(symbol)
        if not allowed:
            return None

        # Taille de position = % du solde ACTUEL (pas du capital de départ)
        position_size_usd = self.current_balance * MAX_POSITION_PCT
        quantity = position_size_usd / entry_price

        # SL et TP basés sur l'ATR (volatilité réelle de la crypto)
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
        )

    # ------------------------------------------------------------------
    # 4. ENREGISTRER / FERMER UNE POSITION
    # ------------------------------------------------------------------
    def register_open_position(self, sizing: PositionSizing):
        self.open_positions[sizing.symbol] = sizing

    def close_position(self, symbol: str, exit_price: float) -> float:
        """
        Ferme une position, met à jour le solde, retourne le profit/perte réalisé.
        """
        if symbol not in self.open_positions:
            return 0.0
        pos = self.open_positions.pop(symbol)
        pnl = pos.quantity * (exit_price - pos.entry_price)
        self.update_balance(self.current_balance + pnl)
        return round(pnl, 2)


def calculate_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    """
    Calcule l'ATR (Average True Range) — mesure standard de la volatilité.
    Nécessite les séries de prix High, Low, Close (les X dernières bougies).
    """
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
