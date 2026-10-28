import numpy as np

from indicators import (
    moving_average, rsi, adx, bollinger_bands, volume_ratio,
    is_reversal_candle, background_trend_is_bearish,
    ema_series, adx_wilder, atr_wilder,
)
from signal import Signal, no_signal

MIN_CONVICTION_TO_ACT = 50


class TrendFollowingStrategy:
    name = "trend_following"

    def __init__(self, short_period=20, long_period=50, adx_min=20):
        self.short_period = short_period
        self.long_period = long_period
        self.adx_min = adx_min

    def get_signal(self, candles, context=None):
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
        current_adx = adx(highs, lows, closes)
        if current_adx > self.adx_min:
            sig.add(30, f"ADX confirme une tendance ({current_adx:.0f})")
        else:
            sig.add(-25, f"ADX faible ({current_adx:.0f})")
        vol_ratio = volume_ratio(volumes)
        if vol_ratio > 1.2:
            sig.add(20, f"Volume confirmé ({vol_ratio:.1f}x)")
        elif vol_ratio < 0.7:
            sig.add(-15, f"Volume faible ({vol_ratio:.1f}x)")
        if sig.conviction_score < MIN_CONVICTION_TO_ACT:
            return no_signal()
        return sig


class MeanReversionStrategy:
    name = "mean_reversion"

    def __init__(self, rsi_period=14, oversold=30, overbought=70, adx_max=25):
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.adx_max = adx_max

    def get_signal(self, candles, context=None):
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
        current_adx = adx(highs, lows, closes)
        if current_adx < self.adx_max:
            sig.add(20, f"ADX confirme un range ({current_adx:.0f})")
        else:
            sig.add(-20, f"ADX élevé ({current_adx:.0f})")
        if direction == "buy":
            if current_price <= bands["lower"]:
                sig.add(15, "Prix sous Bollinger basse")
            else:
                sig.add(-10, "Prix pas sur bande basse")
            if background_trend_is_bearish(closes):
                sig.add(-40, "Tendance de fond baissière")
            if is_reversal_candle(candles):
                sig.add(15, "Bougie de retournement")
            else:
                sig.add(-15, "Pas de retournement")
        if sig.conviction_score < MIN_CONVICTION_TO_ACT:
            return no_signal()
        return sig


class AdaptiveHybridStrategy:
    name = "adaptive_hybrid"

    def __init__(self, adx_threshold=25):
        self.adx_threshold = adx_threshold
        self.trend_strategy = TrendFollowingStrategy()
        self.reversion_strategy = MeanReversionStrategy()

    def get_signal(self, candles, context=None):
        highs, lows, closes = candles["high"], candles["low"], candles["close"]
        if len(closes) < 51:
            return no_signal()
        current_adx = adx(highs, lows, closes)
        if current_adx > self.adx_threshold:
            sig = self.trend_strategy.get_signal(candles)
        else:
            sig = self.reversion_strategy.get_signal(candles)
        if sig.is_actionable:
            sig.reasons.insert(0, f"[Mode {'tendance' if current_adx > self.adx_threshold else 'range'} ADX={current_adx:.0f}]")
        return sig


class TrendPullback4HStrategy:
    """
    Stratégie 'Trend Pullback 4H' (spécification Cubrik) -- bougies 4h, contexte 1D.

    LONG : clôture 4h > EMA50(4h) ; clôture 1D > EMA200(1D) ; au moins une des 6 bougies
    PRÉCÉDANT la bougie de reprise a un plus bas <= EMA20 ; la bougie de reprise clôture
    > EMA20 ; ADX(14, Wilder) > 20 ; volume de reprise > 1,2x la moyenne des 20 bougies
    précédentes. SHORT : miroir exact (Futures uniquement : le bot ne shorte jamais en spot).

    Un signal n'est émis qu'à la PREMIÈRE bougie où toutes les conditions deviennent vraies
    (front montant) -> un seul signal par pullback, pas de répétition sur 6 bougies.
    SL = 1,5 ATR, TP = 3 ATR (ATR 14 de Wilder, 4h). Filtre serveur bot 2 : stop entre 1 % et 8 %.
    Sorties : break-even à +1,5 ATR puis trailing 1 ATR (évalués à la clôture 4h, ATR d'entrée),
    invalidation si clôture 4h de l'autre côté de l'EMA50, sortie temporelle à 48 h.
    Le score de conviction est conservé pour le journal/l'IA ; il ne filtre rien en pratique.
    """
    name = "trend_pullback_4h"
    timeframe = "4H"
    needs_context = True
    max_position_age_hours = 48
    trail_mode = "atr_stage"

    def __init__(self, adx_min=20, vol_mult=1.2, stop_pct_range=(0.01, 0.08),
                 strict_adx=True, strict_volume=True, regime_hi=60, regime_lo=40):
        self.adx_min = adx_min
        self.vol_mult = vol_mult
        self.stop_pct_range = stop_pct_range
        self.strict_adx = strict_adx
        self.strict_volume = strict_volume
        self.regime_hi = regime_hi
        self.regime_lo = regime_lo

    def _setup(self, candles, daily_close, side):
        closes, highs, lows, vols = candles["close"], candles["high"], candles["low"], candles["volume"]
        n = len(closes)
        if n < 60 or len(daily_close) < 200:
            return None
        ema20 = ema_series(closes, 20)
        ema50 = ema_series(closes, 50)
        d_ema200 = ema_series(daily_close, 200)[-1]
        if np.isnan(ema50[-1]) or np.isnan(d_ema200):
            return None
        price = float(closes[-1])
        daily_last = float(daily_close[-1])
        if side == "long":
            trend_ok = price > ema50[-1] and daily_last > d_ema200
            touched = any(lows[k] <= ema20[k] for k in range(n - 7, n - 1))
            reclaim = price > ema20[-1]
        else:
            trend_ok = price < ema50[-1] and daily_last < d_ema200
            touched = any(highs[k] >= ema20[k] for k in range(n - 7, n - 1))
            reclaim = price < ema20[-1]
        if not (trend_ok and touched and reclaim):
            return None
        adx_val = adx_wilder(highs, lows, closes)
        vol_avg = float(np.mean(vols[-21:-1]))
        vol_ratio = float(vols[-1]) / vol_avg if vol_avg > 0 else 0.0
        if self.strict_adx and adx_val <= self.adx_min:
            return None
        if self.strict_volume and vol_ratio <= self.vol_mult:
            return None
        return {"adx": adx_val, "vol_ratio": vol_ratio, "price": price,
                "ema50": float(ema50[-1]), "daily_ema200": float(d_ema200), "daily_last": daily_last}

    def get_signal(self, candles, context=None):
        daily = (context or {}).get("daily")
        if daily is None:
            return no_signal()
        daily_close = daily["close"]
        for side, direction in (("long", "buy"), ("short", "sell")):
            now = self._setup(candles, daily_close, side)
            if now is None:
                continue
            prev_candles = {k: v[:-1] for k, v in candles.items()}
            if self._setup(prev_candles, daily_close, side) is not None:
                continue   # setup déjà vrai à la bougie précédente : signal déjà émis
            return self._build_signal(direction, side, now, candles, context)
        return no_signal()

    def _build_signal(self, direction, side, now, candles, context):
        atr = atr_wilder(candles["high"], candles["low"], candles["close"])
        price = now["price"]
        if price <= 0 or atr <= 0:
            return no_signal()
        stop_pct = 1.5 * atr / price
        lo, hi = self.stop_pct_range
        if not (lo <= stop_pct <= hi):
            return no_signal()

        sig = Signal(direction=direction, conviction_score=0.0)
        sig.atr = atr
        sig.add(40, f"Pullback EMA20 + reprise 4h ({side})")
        if now["adx"] > self.adx_min:
            sig.add(25, f"ADX Wilder {now['adx']:.0f} > {self.adx_min}")
        else:
            sig.add(-20, f"ADX Wilder {now['adx']:.0f} faible")
        if now["vol_ratio"] > self.vol_mult:
            sig.add(15, f"Volume de reprise {now['vol_ratio']:.1f}x la moyenne")
        else:
            sig.add(-10, f"Volume de reprise faible ({now['vol_ratio']:.1f}x)")
        if side == "long":
            aligned = price > now["ema50"] and now["daily_last"] > now["daily_ema200"]
        else:
            aligned = price < now["ema50"] and now["daily_last"] < now["daily_ema200"]
        if aligned:
            sig.add(20, "Contexte 4h (EMA50) et 1D (EMA200) alignés")
        else:
            sig.add(-15, "Contexte multi-timeframe contre le trade")

        pct = (context or {}).get("regime_bullish_pct")
        if pct is not None:
            if side == "long":
                fav, unfav = pct >= self.regime_hi, pct <= self.regime_lo
            else:
                fav, unfav = pct <= self.regime_lo, pct >= self.regime_hi
            if fav:
                sig.add(10, f"Régime du panier favorable ({pct:.0f}% haussier)")
            elif unfav:
                sig.add(-20, f"Régime du panier défavorable ({pct:.0f}% haussier)")

        sig.conviction_score = min(100.0, sig.conviction_score)
        if sig.conviction_score < MIN_CONVICTION_TO_ACT:
            return no_signal()
        return sig

    def exit_direction(self, candles):
        """'sell' = clôture 4h sous l'EMA50 (invalide les longs) ; 'buy' = au-dessus (invalide les shorts)."""
        closes = candles["close"]
        if len(closes) < 60:
            return "none"
        e50 = ema_series(closes, 50)[-1]
        if np.isnan(e50):
            return "none"
        if closes[-1] < e50:
            return "sell"
        if closes[-1] > e50:
            return "buy"
        return "none"


ALL_STRATEGIES = [TrendFollowingStrategy(), MeanReversionStrategy(), AdaptiveHybridStrategy(), TrendPullback4HStrategy()]
