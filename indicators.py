import numpy as np


def moving_average(prices, period):
    return float(np.mean(prices[-period:]))


def rsi(prices, period=14):
    deltas = np.diff(prices[-(period + 1):])
    gains = deltas[deltas > 0].sum()
    losses = -deltas[deltas < 0].sum()
    if losses == 0:
        return 100.0
    rs = gains / losses
    return float(100 - (100 / (1 + rs)))


def adx(high, low, close, period=14):
    high, low, close = np.asarray(high, float), np.asarray(low, float), np.asarray(close, float)
    up_move = np.diff(high)
    down_move = -np.diff(low)
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    prev_close = close[:-1]
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)))
    atr = np.mean(tr[-period:])
    if atr == 0:
        return 0.0
    plus_di = 100 * np.mean(plus_dm[-period:]) / atr
    minus_di = 100 * np.mean(minus_dm[-period:]) / atr
    if (plus_di + minus_di) == 0:
        return 0.0
    return float(100 * abs(plus_di - minus_di) / (plus_di + minus_di))


def bollinger_bands(prices, period=20, num_std=2.0):
    window = prices[-period:]
    mean = np.mean(window)
    std = np.std(window)
    return {"upper": float(mean + num_std * std), "middle": float(mean), "lower": float(mean - num_std * std)}


def volume_ratio(volumes, period=20):
    avg_volume = np.mean(volumes[-period:-1]) if len(volumes) > period else np.mean(volumes[:-1])
    if avg_volume == 0:
        return 1.0
    return float(volumes[-1] / avg_volume)


def is_reversal_candle(candles):
    opens, closes = candles["open"], candles["close"]
    if len(closes) < 2:
        return False
    return bool(closes[-1] > opens[-1] and closes[-1] > closes[-2])


def background_trend_is_bearish(closes, period=200):
    usable_period = min(period, len(closes) - 1)
    if usable_period < 20:
        return False
    long_ma = moving_average(closes, usable_period)
    return bool(closes[-1] < long_ma * 0.97)


# ----------------------------------------------------------------------
# Indicateurs "Wilder" utilisés par la stratégie Trend Pullback 4H
# ----------------------------------------------------------------------
def ema_series(prices, period):
    """EMA classique (initialisée sur la moyenne simple des `period` premières valeurs).
    Renvoie un tableau de même longueur (NaN avant `period` valeurs)."""
    prices = np.asarray(prices, dtype=float)
    out = np.full(len(prices), np.nan)
    if len(prices) < period:
        return out
    alpha = 2.0 / (period + 1)
    out[period - 1] = prices[:period].mean()
    for i in range(period, len(prices)):
        out[i] = alpha * prices[i] + (1 - alpha) * out[i - 1]
    return out


def _true_range(high, low, close):
    prev_close = close[:-1]
    return np.maximum(high[1:] - low[1:],
                      np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)))


def atr_wilder(high, low, close, period=14):
    """ATR avec lissage de Wilder."""
    high, low, close = np.asarray(high, float), np.asarray(low, float), np.asarray(close, float)
    if len(close) <= period:
        return float(np.mean(high - low)) if len(close) else 0.0
    tr = _true_range(high, low, close)
    atr_val = float(np.mean(tr[:period]))
    for v in tr[period:]:
        atr_val = (atr_val * (period - 1) + v) / period
    return float(atr_val)


def adx_wilder(high, low, close, period=14):
    """ADX avec lissage de Wilder (DX lissé), comme demandé par la spécification."""
    high, low, close = np.asarray(high, float), np.asarray(low, float), np.asarray(close, float)
    if len(close) < 2 * period + 1:
        return 0.0
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = _true_range(high, low, close)

    def dx_of(tr_s, p_s, m_s):
        if tr_s == 0:
            return 0.0
        pdi, mdi = 100 * p_s / tr_s, 100 * m_s / tr_s
        den = pdi + mdi
        return 0.0 if den == 0 else 100 * abs(pdi - mdi) / den

    tr_s, p_s, m_s = tr[:period].sum(), plus_dm[:period].sum(), minus_dm[:period].sum()
    dxs = [dx_of(tr_s, p_s, m_s)]
    for i in range(period, len(tr)):
        tr_s = tr_s - tr_s / period + tr[i]
        p_s = p_s - p_s / period + plus_dm[i]
        m_s = m_s - m_s / period + minus_dm[i]
        dxs.append(dx_of(tr_s, p_s, m_s))
    if len(dxs) < period:
        return float(np.mean(dxs))
    adx_val = float(np.mean(dxs[:period]))
    for v in dxs[period:]:
        adx_val = (adx_val * (period - 1) + v) / period
    return float(adx_val)
