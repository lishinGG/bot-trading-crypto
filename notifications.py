"""
notifications.py
=================
Envoie des notifications Telegram en temps réel pour les événements
importants du bot (trade ouvert/fermé, arrêt de sécurité, pause).

Principe de robustesse identique au reste du bot : si l'envoi échoue
pour n'importe quelle raison (clé manquante, panne Telegram, pas de
connexion), le bot continue de fonctionner normalement -- une
notification manquée n'est jamais une raison d'interrompre le trading.
"""

import os
import requests


def send_telegram_message(text: str):
    """Envoie un message Telegram. Échoue silencieusement (avec un simple
    print dans les logs) si les clés sont absentes ou si l'envoi échoue."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        return   # notifications non configurées -- pas une erreur, juste désactivées

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        print(f"[notifications] Envoi Telegram échoué (le bot continue normalement) : {e}")


def notify_trade_opened(strategy_name: str, symbol: str, price: float, note: str = ""):
    text = f"🟢 ACHAT\nStratégie : {strategy_name}\nCrypto : {symbol}\nPrix : {price}"
    if note:
        text += f"\n{note}"
    send_telegram_message(text)


def notify_trade_closed(strategy_name: str, symbol: str, price: float, pnl: float, reason: str):
    emoji = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")
    text = (f"{emoji} POSITION FERMÉE\nStratégie : {strategy_name}\nCrypto : {symbol}\n"
            f"Prix : {price}\nRésultat : {pnl:+.2f}$\nRaison : {reason}")
    send_telegram_message(text)


def notify_circuit_breaker(strategy_name: str):
    text = (f"🚨 ARRÊT DE SÉCURITÉ\nLa stratégie {strategy_name} vient d'atteindre "
            f"-20% de perte cumulée et arrête d'ouvrir de nouvelles positions.")
    send_telegram_message(text)


def notify_strategy_paused(strategy_name: str):
    text = (f"⏸️ STRATÉGIE EN PAUSE\n{strategy_name} vient d'être mise en pause "
            f"suite à une dégradation de sa performance récente.")
    send_telegram_message(text)
