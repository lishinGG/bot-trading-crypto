from signal import Signal
from ai_providers import ask_ai_opinion
import config


def build_ai_context(symbol: str, strategy_name: str, signal: Signal, current_price: float,
                      market_context: str = "", memory_context: str = "", is_futures: bool = False,
                      leverage: float = 1.0, side: str = "long") -> str:
    reasons_text = "\n".join(f"  - {r}" for r in signal.reasons)
    context = f"""Crypto : {symbol}
Stratégie : {strategy_name}
Direction proposée : {signal.direction.upper()}
Sens de la position : {side.upper()}
Mode : {"FUTURES avec levier " + str(leverage) + "x -- sois particulièrement vigilant, le risque est amplifié" if is_futures else "SPOT (sans levier)"}
Prix actuel : {current_price}
Score de conviction technique (0-100) : {signal.conviction_score:.0f}
Facteurs pris en compte :
{reasons_text}"""
    if market_context:
        context += f"\n\nContexte sur des échéances plus longues et le marché global :\n{market_context}"
    if memory_context:
        context += f"\n\n{memory_context}"
    return context


def evaluate(symbol: str, strategy_name: str, signal: Signal, current_price: float,
             market_context: str = "", memory_context: str = "", is_futures: bool = False,
             leverage: float = 1.0, side: str = "long") -> Signal:
    if not signal.is_actionable:
        return signal
    if not config.USE_AI_LAYER:
        return signal

    context = build_ai_context(symbol, strategy_name, signal, current_price,
                                market_context, memory_context, is_futures, leverage, side)
    ai_opinion = ask_ai_opinion(context)

    if ai_opinion is None:
        signal.reasons.append("[IA indisponible -- décision basée sur le Niveau 1 uniquement]")
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
        signal.reasons.append(f"[IA a approuvé, confiance {ai_confidence:.0f} : {ai_opinion.get('reason', '')}]")

    return signal
