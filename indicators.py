"""
indicators.py
=============
Calculs des indicateurs techniques utilisés par les 3 stratégies.
Pas de bibliothèque externe compliquée — tout est fait à la main avec
numpy, pour rester simple et transparent sur ce que chaque calcul fait.
"""

import numpy as np


def moving_average(prices: np.ndarray, period: int) -> float:
    """Moyenne mobile simple sur les `period` dernières valeurs."""
    return float(np.mean(prices[-period:]))


def rsi(prices: np.ndarray, period: int = 14) -> float:
    """
    RSI (Relative Strength Index) — oscille entre 0 et 100.
    < 30 = zone de survente (potentiel rebond haussier)
    > 70 = zone de surachat (potentiel repli baissier)
    """
    deltas = np.diff(prices[-(period + 1):])
    gains = deltas[deltas > 0].sum()
    losses = -deltas[deltas < 0].sum()

    if losses == 0:
        return 100.0
    rs = gains / losses
    return float(100 - (100 / (1 + rs)))


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    """
    ADX (Average Directional Index) — mesure la FORCE d'une tendance
    (peu importe la direction). Ne dit pas si ça monte ou descend, juste
    si le marché est "en tendance" ou "en range" (qui oscille sans but).

    > 25 = tendance forte -> le suivi de tendance fonctionne mieux
    < 25 = marché en range -> l'achat des creux fonctionne mieux

    C'est cet indicateur qui pilote la Stratégie 3 (mix adaptatif).
    """
    high, low, close = np.asarray(high, float), np.asarray(low, float), np.asarray(close, float)
    up_move = np.diff(high)
    down_move = -np.diff(low)

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    prev_close = close[:-1]
    tr = np.maximum(high[1:] - low[1:],
                     np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)))

    atr = np.mean(tr[-period:])
    if atr == 0:
        return 0.0

    plus_di = 100 * np.mean(plus_dm[-period:]) / atr
    minus_di = 100 * np.mean(minus_dm[-period:]) / atr

    if (plus_di + minus_di) == 0:
        return 0.0
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    return float(dx)
