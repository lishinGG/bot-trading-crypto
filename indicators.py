"""
indicators.py
=============
Calculs des indicateurs techniques utilisés par les stratégies.
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

    > 20-25 = tendance -> le suivi de tendance fonctionne mieux
    < 20-25 = range     -> l'achat des creux fonctionne mieux
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


def bollinger_bands(prices: np.ndarray, period: int = 20, num_std: float = 2.0) -> dict:
    """
    Bandes de Bollinger — une bande haute et une bande basse autour de la
    moyenne, qui s'élargissent quand le marché est volatile et se
    resserrent quand il est calme.

    Utilisé par la Stratégie 2 pour confirmer un VRAI creux : le prix doit
    toucher la bande basse, pas juste avoir un RSI bas (le RSI seul peut
    donner de faux signaux, la bande basse confirme un excès statistique
    réel du prix par rapport à sa moyenne récente).
    """
    window = prices[-period:]
    mean = np.mean(window)
    std = np.std(window)
    return {
        "upper": float(mean + num_std * std),
        "middle": float(mean),
        "lower": float(mean - num_std * std),
    }


def volume_ratio(volumes: np.ndarray, period: int = 20) -> float:
    """
    Ratio entre le volume actuel et le volume moyen récent.
    > 1.0 = volume au-dessus de la moyenne (mouvement "confirmé")
    < 1.0 = volume en-dessous de la moyenne (mouvement suspect, peu de conviction)
    """
    avg_volume = np.mean(volumes[-period:-1]) if len(volumes) > period else np.mean(volumes[:-1])
    if avg_volume == 0:
        return 1.0
    return float(volumes[-1] / avg_volume)


def is_reversal_candle(candles: dict) -> bool:
    """
    Vérifie qu'une vraie bougie de retournement s'est formée (pas juste
    un indicateur en survente). On demande que la dernière bougie clôture
    plus haut qu'elle n'a ouvert (bougie verte) ET plus haut que la
    clôture précédente -- un vrai début de rebond, pas juste "le RSI dit
    que c'est bas".
    """
    opens, closes = candles["open"], candles["close"]
    if len(closes) < 2:
        return False
    last_is_green = closes[-1] > opens[-1]
    higher_than_prev_close = closes[-1] > closes[-2]
    return bool(last_is_green and higher_than_prev_close)


def background_trend_is_bearish(closes: np.ndarray, period: int = 200) -> bool:
    """
    Vérifie la tendance de fond (moyenne longue, ex: 200 périodes) pour
    éviter d'acheter un "creux" en pleine tendance baissière de fond --
    l'erreur classique du RSI qui reste en survente pendant une chute
    prolongée. Si pas assez de données pour 200 périodes, utilise ce qui
    est disponible plutôt que de bloquer la stratégie.
    """
    usable_period = min(period, len(closes) - 1)
    if usable_period < 20:
        return False  # pas assez de données pour juger, on ne bloque pas
    long_ma = moving_average(closes, usable_period)
    return bool(closes[-1] < long_ma * 0.97)  # prix nettement sous sa MM longue
