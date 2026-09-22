"""
okx_client.py
=============
Couche au-dessus du SDK officiel OKX (python-okx). Gère à la fois le
SPOT (achat/vente classique) et les FUTURES perpétuels (contrats à
levier) -- toujours en mode DÉMO (flag="1").

IMPORTANT : flag="1" = mode DÉMO. Ne JAMAIS mettre flag="0" (mode réel).
"""

import os
import numpy as np
from okx import MarketData, Account, Trade, PublicData


class OKXDemoClient:
    def __init__(self):
        api_key = os.environ["OKX_API_KEY"]
        api_secret = os.environ["OKX_API_SECRET"]
        passphrase = os.environ["OKX_PASSPHRASE"]

        flag = "1"  # 1 = démo, 0 = réel — NE JAMAIS CHANGER dans ce projet

        self.market_api = MarketData.MarketAPI(api_key, api_secret, passphrase, False, flag)
        self.account_api = Account.AccountAPI(api_key, api_secret, passphrase, False, flag)
        self.trade_api = Trade.TradeAPI(api_key, api_secret, passphrase, False, flag)
        self.public_api = PublicData.PublicAPI(flag=flag)
        self._instrument_specs_cache = {}

    # ------------------------------------------------------------------
    # DONNÉES DE MARCHÉ (communes spot/futures -- un instId suffit)
    # ------------------------------------------------------------------
    def get_candles(self, symbol: str, bar: str = "1H", limit: int = 100):
        """Récupère les X dernières bougies COMPLÈTES (retire la bougie en cours)."""
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
    # SPOT
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
            instId=symbol, algoId=algo_id,
            newSlTriggerPx=str(new_stop_loss_price), newSlOrdPx="-1",
        )

    # ------------------------------------------------------------------
    # FUTURES (contrats perpétuels à levier -- instId du type "BTC-USDT-SWAP")
    # ------------------------------------------------------------------
    def get_instrument_specs(self, inst_id: str) -> dict:
        """
        Récupère (et met en cache) les spécifications d'un contrat perpétuel :
        - ctVal : la quantité d'actif que représente 1 contrat (ex: 0.01 BTC)
        - lotSz : l'incrément minimum de contrats (souvent 1)
        - minSz : la taille minimum d'ordre en contrats
        """
        if inst_id in self._instrument_specs_cache:
            return self._instrument_specs_cache[inst_id]

        result = self.public_api.get_instruments(instType="SWAP", instId=inst_id)
        data = result["data"][0]
        specs = {
            "ctVal": float(data["ctVal"]),
            "lotSz": float(data["lotSz"]),
            "minSz": float(data["minSz"]),
        }
        self._instrument_specs_cache[inst_id] = specs
        return specs

    def set_leverage(self, inst_id: str, leverage: int, mgn_mode: str = "isolated"):
        return self.account_api.set_leverage(instId=inst_id, lever=str(leverage), mgnMode=mgn_mode)

    def convert_to_contracts(self, inst_id: str, asset_quantity: float) -> float:
        """
        OKX exprime les ordres Futures en NOMBRE DE CONTRATS, pas
        directement en quantité d'actif (BTC, ETH...). Cette fonction fait
        la conversion : quantité d'actif -> nombre de contrats, arrondi au
        pas autorisé (lotSz) et jamais en dessous du minimum (minSz).
        """
        specs = self.get_instrument_specs(inst_id)
        raw_contracts = asset_quantity / specs["ctVal"]
        rounded = round(raw_contracts / specs["lotSz"]) * specs["lotSz"]
        return max(rounded, specs["minSz"])

    def place_futures_open_long(self, inst_id: str, contracts: float, mgn_mode: str = "isolated"):
        """Ouvre une position LONGUE à levier (achat de contrats perpétuels)."""
        return self.trade_api.place_order(
            instId=inst_id, tdMode=mgn_mode, side="buy", posSide="long",
            ordType="market", sz=str(contracts),
        )

    def place_futures_close_long(self, inst_id: str, contracts: float, mgn_mode: str = "isolated"):
        """Ferme une position longue (vend les contrats détenus)."""
        return self.trade_api.place_order(
            instId=inst_id, tdMode=mgn_mode, side="sell", posSide="long",
            ordType="market", sz=str(contracts),
        )

    def place_futures_stop_loss_take_profit(self, inst_id: str, contracts: float,
                                             stop_loss_price: float, take_profit_price: float,
                                             mgn_mode: str = "isolated") -> str:
        """Ordre OCO pour une position longue à levier."""
        result = self.trade_api.place_algo_order(
            instId=inst_id, tdMode=mgn_mode, side="sell", posSide="long", ordType="oco",
            sz=str(contracts),
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
            instId=inst_id, algoId=algo_id,
            newSlTriggerPx=str(new_stop_loss_price), newSlOrdPx="-1",
        )

    def get_futures_realized_pnl(self, inst_id: str) -> float:
        """
        Récupère le P&L RÉEL (frais + funding déjà déduits par OKX) de la
        position Futures la plus récemment fermée sur cet instrument,
        via l'historique des positions d'OKX.

        Lève une exception si la récupération échoue (le code appelant
        doit prévoir un repli sur le calcul interne simplifié -- on ne
        veut jamais bloquer une fermeture de position pour ça).
        """
        result = self.account_api.get_positions_history(instType="SWAP", instId=inst_id, limit="1")
        record = result["data"][0]
        # Le champ s'appelle "pnl" dans l'API OKX v5 -- inclut déjà le
        # résultat net après frais ("fee") et funding ("fundingFee").
        return float(record["pnl"])
