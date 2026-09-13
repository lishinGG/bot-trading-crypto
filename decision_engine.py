"""
decision_engine.py
===================
Le point de passage unique entre "un signal technique a été détecté" et
"le bot doit vraiment agir". C'est ici que le Niveau 1 (score technique)
et le Niveau 2 (avis IA) se combinent.

GARANTIE D'INDÉPENDANCE : si l'IA est désactivée, indisponible, ou en
échec pour quelque raison que ce soit, ce module fonctionne uniquement
avec le score du Niveau 1. Aucune dépendance obligatoire à l'IA.
"""

from signal import Signal
from ai_providers import ask_ai_opinion
import config


def build_ai_context(symbol: str, strategy_name: str, signal: Signal, current_price: float,
                      market_context: str = "") -> str:
    """Construit le résumé texte envoyé à l'IA pour validation."""
    reasons_text = "\n".join(f"  - {r}" for r in signal.reasons)
    context = f"""Crypto : {symbol}
Stratégie : {strategy_name}
Direction proposée : {signal.direction.upper()}
Prix actuel : {current_price}
Score de conviction technique (0-100) : {signal.conviction_score:.0f}
Facteurs pris en compte :
{reasons_text}"""
    if market_context:
        context += f"\n\nContexte sur des échéances plus longues :\n{market_context}"
    return context


def evaluate(symbol: str, strategy_name: str, signal: Signal, current_price: float,
             market_context: str = "") -> Signal:
    """
    Prend un signal du Niveau 1 et l'enrichit avec l'avis de l'IA si elle
    est activée et disponible. Renvoie TOUJOURS un Signal exploitable,
    que l'IA ait répondu ou non.

    market_context (optionnel) : résumé texte de la tendance sur des
    échéances plus longues (4h, 1 jour), pour que l'IA puisse juger si le
    signal horaire va dans le même sens que la tendance de fond.
    """
    if not signal.is_actionable:
        return signal

    if not config.USE_AI_LAYER:
        return signal  # Niveau 1 seul, comportement normal si l'IA est éteinte

    context = build_ai_context(symbol, strategy_name, signal, current_price, market_context)
    ai_opinion = ask_ai_opinion(context)

    if ai_opinion is None:
        # L'IA n'a pas pu répondre (limite atteinte, panne...) -- on
        # continue avec le score du Niveau 1 seul, comme prévu.
        signal.reasons.append("[IA indisponible -- décision basée sur le Niveau 1 uniquement]")
        return signal

    signal.ai_reviewed = True
    signal.ai_notes = ai_opinion.get("reason", "")

    if ai_opinion["decision"] == "REJECT":
        signal.direction = "none"
        signal.reasons.append(f"[IA a rejeté ce trade : {ai_opinion.get('reason', '')}]")
    else:
        ai_confidence = float(ai_opinion.get("confidence", 50))
        signal.conviction_score = min(100, (signal.conviction_score + ai_confidence) / 2 + 10)

        signal.ai_size_factor = float(ai_opinion.get("size_factor", 1.0))
        signal.ai_sl_tightness = float(ai_opinion.get("sl_tightness", 1.0))
        signal.ai_tp_extension = float(ai_opinion.get("tp_extension", 1.0))

        signal.reasons.append(f"[IA a approuvé, confiance {ai_confidence:.0f} : {ai_opinion.get('reason', '')}]")

    return signal
