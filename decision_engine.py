from signal import Signal
from ai_providers import ask_ai_opinion
import config


def build_ai_context(symbol, strategy_name, signal, current_price, market_context="", leverage=None):
    reasons_text = "\n".join(f"  - {r}" for r in signal.reasons)
    context = f"""Crypto : {symbol}
Stratégie : {strategy_name}
Direction proposée : {signal.direction.upper()}
Prix actuel : {current_price}
Score technique : {signal.conviction_score:.0f}
Facteurs :
{reasons_text}"""
    if market_context:
        context += f"\n\nContexte plus long terme:\n{market_context}"
    if leverage:
        context += (f"\n\nATTENTION : ce trade utilise un LEVIER de {leverage}x (contrat Futures perpétuel, "
                     f"pas du spot). Le risque en dollars est calculé pour rester identique à un trade spot "
                     f"équivalent -- mais sois particulièrement vigilant sur la fiabilité du signal, puisqu'un "
                     f"contrat à levier est plus sensible à la volatilité à court terme et aux mèches de prix.")
    return context


def evaluate(symbol, strategy_name, signal, current_price, market_context="", leverage=None):
    if not signal.is_actionable:
        return signal
    if not config.USE_AI_LAYER:
        return signal
    context = build_ai_context(symbol, strategy_name, signal, current_price, market_context, leverage)
    ai_opinion = ask_ai_opinion(context)
    if ai_opinion is None:
        signal.reasons.append("[IA indisponible -- Niveau 1 uniquement]")
        return signal
    signal.ai_reviewed = True
    signal.ai_notes = ai_opinion.get("reason", "")
    if ai_opinion["decision"] == "REJECT":
        signal.direction = "none"
        signal.reasons.append(f"[IA a rejeté : {ai_opinion.get('reason', '')}]")
    else:
        ai_confidence = float(ai_opinion.get("confidence", 50))
        signal.conviction_score = min(100, (signal.conviction_score + ai_confidence) / 2 + 10)
        signal.ai_size_factor = float(ai_opinion.get("size_factor", 1.0))
        signal.ai_sl_tightness = float(ai_opinion.get("sl_tightness", 1.0))
        signal.ai_tp_extension = float(ai_opinion.get("tp_extension", 1.0))
        signal.reasons.append(f"[IA a approuvé, confiance {ai_confidence:.0f}]")
    return signal
