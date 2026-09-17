"""
notifications.py
=================
Envoie une notification Telegram à chaque événement important du bot
(achat, fermeture de position, arrêt de sécurité...). Utilise directement
l'API HTTP de Telegram -- pas besoin de bibliothèque spécifique.

Comme pour l'IA, principe de robustesse : si l'envoi échoue pour
n'importe quelle raison, le bot continue de fonctionner normalement.
"""

import os
import config

try:
    import requests
except ImportError:
    requests = None


def send_notification(message: str):
    if not config.USE_NOTIFICATIONS:
        return
    if requests is None:
        print("[notifications] Le paquet 'requests' n'est pas installé, notification ignorée.")
        return

    try:
        token = os.environ["TELEGRAM_BOT_TOKEN"]
        chat_id = os.environ["TELEGRAM_CHAT_ID"]
    except KeyError:
        print("[notifications] TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant, notification ignorée.")
        return

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
    except Exception as e:
        print(f"[notifications] Échec de l'envoi Telegram : {e}")


def notify_trade_opened(strategy_name: str, symbol: str, price: float, size_usd: float,
                         sl: float, tp: float, ai_note: str = ""):
    message = (
        f"🟢 ACHAT — {strategy_name}\n"
        f"{symbol} @ {price}\n"
        f"Taille : {size_usd:.2f}$\n"
        f"SL : {sl} | TP : {tp}"
    )
    if ai_note:
        message += f"\nIA : {ai_note}"
    send_notification(message)


def notify_trade_closed(strategy_name: str, symbol: str, price: float, pnl: float, reason: str):
    emoji = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")
    message = (
        f"{emoji} FERMETURE — {strategy_name}\n"
        f"{symbol} @ {price}\n"
        f"Résultat : {pnl:+.2f}$\n"
        f"Raison : {reason}"
    )
    send_notification(message)


def notify_circuit_breaker(strategy_name: str):
    send_notification(
        f"🛑 CIRCUIT BREAKER DÉCLENCHÉ — {strategy_name}\n"
        f"Perte cumulée >= 20% du capital de départ. Plus de nouvelles positions pour cette stratégie."
    )


def notify_strategy_paused(strategy_name: str):
    send_notification(
        f"⏸️ STRATÉGIE EN PAUSE — {strategy_name}\n"
        f"Performance récente dégradée. La stratégie continue d'être surveillée."
    )
