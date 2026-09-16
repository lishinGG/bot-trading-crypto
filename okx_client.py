"""
okx_client.py
=============
Petite couche au-dessus du SDK officiel OKX (python-okx) pour simplifier
tout ce dont le bot a besoin : récupérer les prix récents, la balance du
compte démo, et passer des ordres spot avec stop-loss/take-profit attachés.

IMPORTANT : flag="1" = mode DÉMO. Ne JAMAIS mettre flag="0" (mode réel)
sans en avoir pleinement conscience — ce projet est conçu pour rester en
démo.
"""

import os
import time
import numpy as np
from okx import MarketData, Account, Trade


class OKXDemoClient:
    def __init__(self):
        api_key = os.environ["OKX_API_KEY"]
        api_secret = os.environ["OKX_API_SECRET"]
        passphrase = os.environ["OKX_PASSPHRASE"]

        flag = "1"  # 1 = démo, 0 = réel — NE JAMAIS CHANGER dans ce projet

        self.market_api = MarketData.MarketAPI(api_key, api_secret, passphrase, False, flag)
        self.account_api = Account.AccountAPI(api_key, api_secret, passphrase, False, flag)
        self.trade_api = Trade.TradeAPI(api_key, api_secret, passphrase, False, flag)

    def get_candles(self, symbol: str, bar: str = "1H", limit: int = 100):
        """
        Récupère les X dernières bougies COMPLÈTES pour une crypto.
        Retire systématiquement la bougie en cours de formation.
        """
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
        """Solde disponible en USDT sur le compte démo."""
        result = self.account_api.get_account_balance(ccy="USDT")
        details = result["data"][0]["details"]
        for d in details:
            if d["ccy"] == "USDT":
                return float(d["availBal"])
        return 0.0

    def place_market_buy(self, symbol: str, quantity: float):
        """Achat immédiat au prix du marché (mode spot)."""
        return self.trade_api.place_order(
            instId=symbol,
            tdMode="cash",
            side="buy",
            ordType="market",
            sz=str(quantity),
        )

    def place_market_sell(self, symbol: str, quantity: float):
        """Vente immédiate au prix du marché (fermeture de position)."""
        return self.trade_api.place_order(
            instId=symbol,
            tdMode="cash",
            side="sell",
            ordType="market",
            sz=str(quantity),
        )

    def place_stop_loss_take_profit(self, symbol: str, quantity: float,
                                     stop_loss_price: float, take_profit_price: float) -> str:
        """
        Place un ordre algo "OCO". Retourne l'algoId de l'ordre créé
        (nécessaire pour le stop-loss suiveur, qui modifie ce même ordre
        plutôt que d'en créer un nouveau).
        """
        result = self.trade_api.place_algo_order(
            instId=symbol,
            tdMode="cash",
            side="sell",
            ordType="oco",
            sz=str(quantity),
            tpTriggerPx=str(take_profit_price),
            tpOrdPx="-1",
            slTriggerPx=str(stop_loss_price),
            slOrdPx="-1",
        )
        try:
            return result["data"][0]["algoId"]
        except (KeyError, IndexError):
            return ""

    def update_stop_loss(self, symbol: str, algo_id: str, new_stop_loss_price: float):
        """
        Modifie le prix de déclenchement du stop-loss d'un ordre OCO déjà
        existant, SANS l'annuler ni en recréer un nouveau -- utilisé par
        le stop-loss suiveur (trailing stop).
        """
        if not algo_id:
            return None
        return self.trade_api.amend_algo_order(
            instId=symbol,
            algoId=algo_id,
            newSlTriggerPx=str(new_stop_loss_price),
            newSlOrdPx="-1",
        )
