"""
strategies.py
=============
Les 3 stratégies. Chacune reçoit les données de marché (bougies récentes)
et renvoie un signal : "buy", "sell", ou None (rien à faire).

Chaque stratégie est une classe indépendante avec la même interface,
pour qu'on puisse facilement les faire tourner en parallèle et comparer
leurs résultats.
"""

from indicators import moving_average, rsi, adx


class TrendFollowingStrategy:
    """
    Stratégie 1 — Suivi de tendance.
    Achète quand la moyenne mobile courte croise au-dessus de la longue
    (signal haussier confirmé), vend quand elle croise en dessous.
    """
    name = "trend_following"

    def __init__(self, short_period: int = 20, long_period: int = 50):
        self.short_period = short_period
        self.long_period = long_period

    def get_signal(self, candles: dict) -> str | None:
        closes = candles["close"]
        if len(closes) < self.long_period + 1:
            return None  # pas assez de données pour calculer les MM

        ma_short_now = moving_average(closes, self.short_period)
        ma_long_now = moving_average(closes, self.long_period)
        ma_short_prev = moving_average(closes[:-1], self.short_period)
        ma_long_prev = moving_average(closes[:-1], self.long_period)

        crossed_up = ma_short_prev <= ma_long_prev and ma_short_now > ma_long_now
        crossed_down = ma_short_prev >= ma_long_prev and ma_short_now < ma_long_now

        if crossed_up:
            return "buy"
        if crossed_down:
            return "sell"
        return None


class MeanReversionStrategy:
    """
    Stratégie 2 — Achat des creux.
    Achète quand le RSI signale une survente (marché "trop vendu"),
    vend quand le RSI signale une surachat ou que le prix revient
    vers sa moyenne.
    """
    name = "mean_reversion"

    def __init__(self, rsi_period: int = 14, oversold: float = 30, overbought: float = 70):
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought

    def get_signal(self, candles: dict) -> str | None:
        closes = candles["close"]
        if len(closes) < self.rsi_period + 1:
            return None

        current_rsi = rsi(closes, self.rsi_period)

        if current_rsi < self.oversold:
            return "buy"
        if current_rsi > self.overbought:
            return "sell"
        return None


class AdaptiveHybridStrategy:
    """
    Stratégie 3 — Mix adaptatif.
    Utilise l'ADX pour détecter si le marché est en tendance ou en range,
    puis applique la logique la plus adaptée à la situation :
      - ADX > 25 (tendance forte) -> suivi de tendance
      - ADX < 25 (range)          -> achat des creux
    """
    name = "adaptive_hybrid"

    def __init__(self, adx_threshold: float = 25):
        self.adx_threshold = adx_threshold
        self.trend_strategy = TrendFollowingStrategy()
        self.reversion_strategy = MeanReversionStrategy()

    def get_signal(self, candles: dict) -> str | None:
        highs, lows, closes = candles["high"], candles["low"], candles["close"]
        if len(closes) < 51:
            return None

        current_adx = adx(highs, lows, closes)

        if current_adx > self.adx_threshold:
            return self.trend_strategy.get_signal(candles)
        else:
            return self.reversion_strategy.get_signal(candles)


# Liste centrale des 3 stratégies, utilisée par main.py pour toutes les
# faire tourner en parallèle sans avoir à les nommer une par une.
ALL_STRATEGIES = [
    TrendFollowingStrategy(),
    MeanReversionStrategy(),
    AdaptiveHybridStrategy(),
]
