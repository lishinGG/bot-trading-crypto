"""
okx_client.py -- couche au-dessus du SDK python-okx : SPOT (toujours long) et FUTURES perpétuels (long ET short).
IMPORTANT : flag="1" = mode DÉMO. Ne JAMAIS mettre flag="0" (mode réel).
"""

import os
import re
import time
import numpy as np
from okx import MarketData, Account, Trade, PublicData


_UNIT_MS = {"m": 60_000, "H": 3_600_000, "D": 86_400_000, "W": 604_800_000}


def _bar_ms(bar: str) -> int:
    """Durée d'une bougie en millisecondes (ex. '1H' -> 3 600 000)."""
    m = re.match(r"(\d+)([mHDW])", bar)
    if not m:
        return 3_600_000
    return int(m.group(1)) * _UNIT_MS[m.group(2)]


def _keep_closed_candles(raw, bar: str, now_ms: int):
    """Garde uniquement les bougies TERMINÉES, triées de la plus ancienne à la plus récente.
    Deux protections : (1) le drapeau 'confirm' d'OKX (dernier champ, '0' = en cours) ;
    (2) l'horloge : une bougie dont l'heure de fin est dans le futur n'est jamais terminée,
    même si OKX la marque confirmée. Renvoie (bougies, nb_retirées_par_horloge)."""
    dur = _bar_ms(bar)
    kept, clock_dropped = [], 0
    for c in sorted(raw, key=lambda x: int(x[0])):
        if str(c[-1]) == "0":
            continue
        if int(c[0]) + dur > now_ms:
            clock_dropped += 1
            continue
        kept.append(c)
    return kept, clock_dropped


class OKXDemoClient:
    def __init__(self):
        api_key = os.environ["OKX_API_KEY"]
        api_secret = os.environ["OKX_API_SECRET"]
        passphrase = os.environ["OKX_PASSPHRASE"]
        flag = "1"  # 1 = démo, 0 = réel — NE JAMAIS CHANGER

        self.market_api = MarketData.MarketAPI(api_key, api_secret, passphrase, False, flag)
        self.account_api = Account.AccountAPI(api_key, api_secret, passphrase, False, flag)
        self.trade_api = Trade.TradeAPI(api_key, api_secret, passphrase, False, flag)
        self.public_api = PublicData.PublicAPI(api_key, api_secret, passphrase, False, flag)
        self._instrument_specs_cache = {}

    # --- données de marché ------------------------------------------------
    def get_candles(self, symbol: str, bar: str = "1H", limit: int = 100):
        """Bougies COMPLÈTES uniquement (jamais celle en cours de formation). Renvoie aussi 'ts' (ms)."""
        result = self.market_api.get_candlesticks(instId=symbol, bar=bar, limit=str(limit + 1))
        raw_all = result["data"]
        now_ms = int(time.time() * 1000)
        raw, clock_dropped = _keep_closed_candles(raw_all, bar, now_ms)
        if clock_dropped:
            print(f"[okx_client] {symbol} {bar} : {clock_dropped} bougie(s) non terminée(s) retirée(s) par contrôle d'horloge "
                  f"(OKX les marquait confirmées).")
        raw = raw[-limit:]
        opens = np.array([float(c[1]) for c in raw])
        highs = np.array([float(c[2]) for c in raw])
        lows = np.array([float(c[3]) for c in raw])
        closes = np.array([float(c[4]) for c in raw])
        volumes = np.array([float(c[5]) for c in raw])
        ts = np.array([int(c[0]) for c in raw])

        # Diagnostic : volume de la dernière bougie anormalement bas -> on trace les données brutes pour comprendre.
        if len(volumes) > 21:
            avg = float(np.mean(volumes[-21:-1]))
            if avg > 0 and volumes[-1] / avg < 0.05:
                tail = sorted(raw_all, key=lambda x: int(x[0]))[-3:]
                print(f"[okx_client][DIAG volume] {symbol} {bar} : volume dernière bougie = {volumes[-1] / avg:.3f}x la moyenne. "
                      f"now_ms={now_ms}. 3 dernières bougies brutes OKX (ts, volume, confirm) : "
                      f"{[(c[0], c[5], c[-1]) for c in tail]}")
        return {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes, "ts": ts}

    def get_current_price(self, symbol: str) -> float:
        result = self.market_api.get_ticker(instId=symbol)
        return float(result["data"][0]["last"])

    def get_balance_usdt(self) -> float:
        result = self.account_api.get_account_balance(ccy="USDT")
        details = result["data"][0]["details"]
        for d in details:
            if d["ccy"] == "USDT":
                return float(d["availBal"])
        return 0.0

    # --- ordres spot (toujours long) ---------------------------------------
    def place_market_buy(self, symbol: str, quantity: float):
        return self.trade_api.place_order(
            instId=symbol, tdMode="cash", side="buy", ordType="market", sz=str(quantity),
        )

    def place_market_sell(self, symbol: str, quantity: float):
        return self.trade_api.place_order(
            instId=symbol, tdMode="cash", side="sell", ordType="market", sz=str(quantity),
        )

    def place_stop_loss_take_profit(self, symbol: str, quantity: float,
                                     stop_loss_price: float, take_profit_price: float) -> str:
        result = self.trade_api.place_algo_order(
            instId=symbol, tdMode="cash", side="sell", ordType="oco", sz=str(quantity),
            tpTriggerPx=str(take_profit_price), tpOrdPx="-1",
            slTriggerPx=str(stop_loss_price), slOrdPx="-1",
        )
        try:
            return result["data"][0]["algoId"]
        except (KeyError, IndexError):
            return ""

    def update_stop_loss(self, symbol: str, algo_id: str, new_stop_loss_price: float):
        if not algo_id:
            return None
        return self.trade_api.amend_algo_order(
            instId=symbol, algoId=algo_id, newSlTriggerPx=str(new_stop_loss_price), newSlOrdPx="-1",
        )

    # --- spécifications d'instrument (quantité <-> contrats) ---------------
    def _get_instrument_spec(self, inst_id: str) -> dict:
        if inst_id not in self._instrument_specs_cache:
            result = self.public_api.get_instruments(instType="SWAP", instId=inst_id)
            data = result["data"][0]
            self._instrument_specs_cache[inst_id] = {
                "ctVal": float(data["ctVal"]),
                "lotSz": float(data["lotSz"]),
                "minSz": float(data["minSz"]),
            }
        return self._instrument_specs_cache[inst_id]

    def convert_to_contracts(self, inst_id: str, asset_quantity: float) -> float:
        spec = self._get_instrument_spec(inst_id)
        raw_contracts = asset_quantity / spec["ctVal"]
        lot = spec["lotSz"]
        rounded = round(raw_contracts / lot) * lot if lot > 0 else raw_contracts
        return max(rounded, spec["minSz"])

    # --- futures : levier --------------------------------------------------
    def set_leverage(self, inst_id: str, leverage: int, mgn_mode: str):
        return self.account_api.set_leverage(instId=inst_id, lever=str(leverage), mgnMode=mgn_mode)

    # --- futures : long ------------------------------------------------------
    def place_futures_open_long(self, inst_id: str, contracts: float, mgn_mode: str):
        return self.trade_api.place_order(
            instId=inst_id, tdMode=mgn_mode, side="buy", posSide="long", ordType="market", sz=str(contracts),
        )

    def place_futures_close_long(self, inst_id: str, contracts: float, mgn_mode: str):
        return self.trade_api.place_order(
            instId=inst_id, tdMode=mgn_mode, side="sell", posSide="long", ordType="market", sz=str(contracts),
        )

    # --- futures : short -----------------------------------------------------
    def place_futures_open_short(self, inst_id: str, contracts: float, mgn_mode: str):
        return self.trade_api.place_order(
            instId=inst_id, tdMode=mgn_mode, side="sell", posSide="short", ordType="market", sz=str(contracts),
        )

    def place_futures_close_short(self, inst_id: str, contracts: float, mgn_mode: str):
        return self.trade_api.place_order(
            instId=inst_id, tdMode=mgn_mode, side="buy", posSide="short", ordType="market", sz=str(contracts),
        )

    # --- futures : SL/TP (OCO) selon le sens ----------------------------------
    def place_futures_stop_loss_take_profit(self, inst_id: str, contracts: float,
                                             stop_loss_price: float, take_profit_price: float,
                                             mgn_mode: str, side: str = "long") -> str:
        close_side = "sell" if side == "long" else "buy"
        result = self.trade_api.place_algo_order(
            instId=inst_id, tdMode=mgn_mode, side=close_side, posSide=side,
            ordType="oco", sz=str(contracts),
            tpTriggerPx=str(take_profit_price), tpOrdPx="-1",
            slTriggerPx=str(stop_loss_price), slOrdPx="-1",
        )
        try:
            return result["data"][0]["algoId"]
        except (KeyError, IndexError):
            return ""

    def update_futures_stop_loss(self, inst_id: str, algo_id: str, new_stop_loss_price: float):
        if not algo_id:
            return None
        return self.trade_api.amend_algo_order(
            instId=inst_id, algoId=algo_id, newSlTriggerPx=str(new_stop_loss_price), newSlOrdPx="-1",
        )

    def get_futures_realized_pnl(self, inst_id: str) -> float:
        result = self.account_api.get_positions_history(instType="SWAP", instId=inst_id, limit="1")
        record = result["data"][0]
        return float(record["pnl"])
