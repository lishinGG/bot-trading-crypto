"""
okx_client.py
=============
Couche au-dessus du SDK officiel OKX (python-okx). Gère le SPOT (achat/
vente classique, toujours long) et les FUTURES perpétuels (contrats à
levier, long ET short).

IMPORTANT : flag="1" = mode DÉMO. Ne JAMAIS mettre flag="0" (mode réel).
"""

import os
import time
import numpy as np
from okx import MarketData, Account, Trade, PublicData


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

    # ------------------------------------------------------------------
    # DONNÉES DE MARCHÉ
    # ------------------------------------------------------------------
    def get_candles(self, symbol: str, bar: str = "1H", limit: int = 100):
        """Récupère les bougies COMPLÈTES (retire celle en cours de formation)."""
        result = self.market_api.get_candlesticks(instId=symbol, bar=bar, limit=str(limit + 1))
        raw = result["data"]
        raw.reverse()
        if raw and raw[-1][-1] == "0":
            raw = raw[:-1]
        raw = raw[-limit:]
        opens = np.array([float(c[1]) for c in raw])
        highs = np.array([float(c[2]) for c in raw])
        lows = np.array([float(c[3]) for c in raw])
        closes = np.array([float(c[4]) for c in raw])
        volumes = np.array([float(c[5]) for c in raw])
        return {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}

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

    # ------------------------------------------------------------------
    # ORDRES SPOT (toujours long)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # SPÉCIFICATIONS D'INSTRUMENT (conversion quantité <-> contrats)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # FUTURES -- LEVIER
    # ------------------------------------------------------------------
    def set_leverage(self, inst_id: str, leverage: int, mgn_mode: str):
        return self.account_api.set_leverage(instId=inst_id, lever=str(leverage), mgnMode=mgn_mode)

    # ------------------------------------------------------------------
    # FUTURES -- OUVERTURE / FERMETURE (long)
    # ------------------------------------------------------------------
    def place_futures_open_long(self, inst_id: str, contracts: float, mgn_mode: str):
        return self.trade_api.place_order(
            instId=inst_id, tdMode=mgn_mode, side="buy", posSide="long", ordType="market", sz=str(contracts),
        )

    def place_futures_close_long(self, inst_id: str, contracts: float, mgn_mode: str):
        return self.trade_api.place_order(
            instId=inst_id, tdMode=mgn_mode, side="sell", posSide="long", ordType="market", sz=str(contracts),
        )

    # ------------------------------------------------------------------
    # FUTURES -- OUVERTURE / FERMETURE (short) -- NOUVEAU
    # ------------------------------------------------------------------
    def place_futures_open_short(self, inst_id: str, contracts: float, mgn_mode: str):
        """Ouvre une position courte : on VEND pour ouvrir (posSide=short)."""
        return self.trade_api.place_order(
            instId=inst_id, tdMode=mgn_mode, side="sell", posSide="short", ordType="market", sz=str(contracts),
        )

    def place_futures_close_short(self, inst_id: str, contracts: float, mgn_mode: str):
        """Ferme une position courte : on RACHÈTE pour fermer (posSide=short)."""
        return self.trade_api.place_order(
            instId=inst_id, tdMode=mgn_mode, side="buy", posSide="short", ordType="market", sz=str(contracts),
        )

    # ------------------------------------------------------------------
    # FUTURES -- SL/TP (OCO), sensible au sens long/short
    # ------------------------------------------------------------------
    def place_futures_stop_loss_take_profit(self, inst_id: str, contracts: float,
                                             stop_loss_price: float, take_profit_price: float,
                                             mgn_mode: str, side: str = "long") -> str:
        """
        Pour un LONG : l'ordre de clôture est un "sell" côté posSide=long.
        Pour un SHORT : l'ordre de clôture est un "buy" côté posSide=short.
        """
        close_side = "sell" if side == "long" else "buy"
        pos_side = side
        result = self.trade_api.place_algo_order(
            instId=inst_id, tdMode=mgn_mode, side=close_side, posSide=pos_side,
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

    # ------------------------------------------------------------------
    # FUTURES -- P&L RÉEL (frais + funding inclus)
    # ------------------------------------------------------------------
    def get_futures_realized_pnl(self, inst_id: str) -> float:
        result = self.account_api.get_positions_history(instType="SWAP", instId=inst_id, limit="1")
        record = result["data"][0]
        return float(record["pnl"])
