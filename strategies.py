"""
strategies.py
=============
Les 3 stratégies, version améliorée. Chacune renvoie maintenant un objet
Signal (voir signal.py) avec une direction, un SCORE DE CONVICTION (0-100)
et le détail des raisons -- au lieu d'un simple "buy"/"sell"/None.

Améliorations apportées suite à la recherche sur l'efficacité réelle des
indicateurs techniques :
  1. Chaque stratégie vérifie qu'elle est dans SON contexte idéal (filtre
     de régime ADX) avant d'agir -- pas seulement la stratégie 3.
  2. La stratégie 2 (creux) est protégée contre les fausses reprises en
     tendance baissière forte (le piège classique du RSI qui reste en
     survente pendant une chute).
  3. Chaque signal porte un score de conviction construit facteur par
     facteur, pas une décision binaire figée.
"""

from indicators import (
    moving_average, rsi, adx, bollinger_bands, volume_ratio,
    is_reversal_candle, background_trend_is_bearish,
)
from signal import Signal, no_signal


# Seuil de conviction minimum pour qu'un signal soit jugé assez fiable
# pour être transmis au risk manager (et, plus tard, à la couche IA).
MIN_CONVICTION_TO_ACT = 55


class TrendFollowingStrategy:
    """
    Stratégie 1 — Suivi de tendance.
    Cœur du signal : croisement MM20/MM50.
    Renforcé par : confirmation ADX (vraie tendance) + confirmation volume.
    """
    name = "trend_following"

    def __init__(self, short_period: int = 20, long_period: int = 50, adx_min: float = 20):
        self.short_period = short_period
        self.long_period = long_period
        self.adx_min = adx_min

    def get_signal(self, candles: dict) -> Signal:
        closes, highs, lows, volumes = candles["close"], candles["high"], candles["low"], candles["volume"]
        if len(closes) < self.long_period + 1:
            return no_signal()

        ma_short_now = moving_average(closes, self.short_period)
        ma_long_now = moving_average(closes, self.long_period)
        ma_short_prev = moving_average(closes[:-1], self.short_period)
        ma_long_prev = moving_average(closes[:-1], self.long_period)

        crossed_up = ma_short_prev <= ma_long_prev and ma_short_now > ma_long_now
        crossed_down = ma_short_prev >= ma_long_prev and ma_short_now < ma_long_now

        if not (crossed_up or crossed_down):
            return no_signal()

        direction = "buy" if crossed_up else "sell"
        sig = Signal(direction=direction, conviction_score=0.0)
        sig.add(40, "Croisement de moyennes mobiles")

        # Filtre de régime : la recherche montre que le suivi de tendance
        # échoue en marché qui oscille -- on exige une tendance confirmée.
        current_adx = adx(highs, lows, closes)
        if current_adx > self.adx_min:
            sig.add(30, f"ADX confirme une tendance ({current_adx:.0f})")
        else:
            sig.add(-25, f"ADX faible, tendance peu fiable ({current_adx:.0f})")

        # Confirmation par le volume : un croisement sans volume est
        # souvent un faux signal.
        vol_ratio = volume_ratio(volumes)
        if vol_ratio > 1.2:
            sig.add(20, f"Volume confirmé ({vol_ratio:.1f}x la moyenne)")
        elif vol_ratio < 0.7:
            sig.add(-15, f"Volume faible ({vol_ratio:.1f}x la moyenne), signal suspect")

        if sig.conviction_score < MIN_CONVICTION_TO_ACT:
            return no_signal()
        return sig


class MeanReversionStrategy:
    """
    Stratégie 2 — Achat des creux.
    Cœur du signal : RSI en survente/surachat.
    Renforcé par : confirmation Bollinger + bougie de retournement réelle
    + protection contre l'achat en pleine tendance baissière de fond
    (le piège identifié dans la recherche : RSI reste bas pendant une
    chute prolongée, acheter dès le premier signal est dangereux).
    """
    name = "mean_reversion"

    def __init__(self, rsi_period: int = 14, oversold: float = 30, overbought: float = 70, adx_max: float = 25):
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.adx_max = adx_max

    def get_signal(self, candles: dict) -> Signal:
        closes, highs, lows = candles["close"], candles["high"], candles["low"]
        if len(closes) < self.rsi_period + 1:
            return no_signal()

        current_rsi = rsi(closes, self.rsi_period)
        bands = bollinger_bands(closes)
        current_price = closes[-1]

        buy_condition = current_rsi < self.oversold
        sell_condition = current_rsi > self.overbought

        if not (buy_condition or sell_condition):
            return no_signal()

        direction = "buy" if buy_condition else "sell"
        sig = Signal(direction=direction, conviction_score=0.0)
        sig.add(35, f"RSI en zone extrême ({current_rsi:.0f})")

        # Filtre de régime : l'achat des creux échoue en tendance forte
        current_adx = adx(highs, lows, closes)
        if current_adx < self.adx_max:
            sig.add(20, f"ADX confirme un marché en range ({current_adx:.0f})")
        else:
            sig.add(-20, f"ADX élevé, marché en tendance forte, creux peu fiable ({current_adx:.0f})")

        if direction == "buy":
            # Confirmation Bollinger : le prix doit vraiment être en excès
            # statistique, pas juste avoir un RSI bas.
            if current_price <= bands["lower"]:
                sig.add(15, "Prix sous la bande de Bollinger basse")
            else:
                sig.add(-10, "Prix pas encore sur la bande basse, creux moins net")

            # Protection anti-piège : ne pas acheter un couteau qui tombe.
            if background_trend_is_bearish(closes):
                sig.add(-40, "Tendance de fond fortement baissière : risque de fausse reprise")

            # Confirmation de retournement réel, pas juste un indicateur bas.
            if is_reversal_candle(candles):
                sig.add(15, "Bougie de retournement confirmée")
            else:
                sig.add(-15, "Pas encore de bougie de retournement, prématuré")

        if sig.conviction_score < MIN_CONVICTION_TO_ACT:
            return no_signal()
        return sig


class AdaptiveHybridStrategy:
    """
    Stratégie 3 — Mix adaptatif.
    Utilise l'ADX pour choisir quelle logique appliquer (tendance ou
    creux), puis réutilise directement le score de conviction de la
    stratégie choisie -- pas de logique dupliquée.
    """
    name = "adaptive_hybrid"

    def __init__(self, adx_threshold: float = 25):
        self.adx_threshold = adx_threshold
        self.trend_strategy = TrendFollowingStrategy()
        self.reversion_strategy = MeanReversionStrategy()

    def get_signal(self, candles: dict) -> Signal:
        highs, lows, closes = candles["high"], candles["low"], candles["close"]
        if len(closes) < 51:
            return no_signal()

        current_adx = adx(highs, lows, closes)

        if current_adx > self.adx_threshold:
            sig = self.trend_strategy.get_signal(candles)
        else:
            sig = self.reversion_strategy.get_signal(candles)

        if sig.is_actionable:
            sig.reasons.insert(0, f"[Mode {'tendance' if current_adx > self.adx_threshold else 'range'} choisi via ADX={current_adx:.0f}]")
        return sig


# Liste centrale des 3 stratégies, utilisée par main.py pour toutes les
# faire tourner en parallèle sans avoir à les nommer une par une.
ALL_STRATEGIES = [
    TrendFollowingStrategy(),
    MeanReversionStrategy(),
    AdaptiveHybridStrategy(),
]
